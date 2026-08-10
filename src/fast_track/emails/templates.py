"""HTML email templates for the Fast Track creator lifecycle reminders.

Copy and visual design (logo header, purple button, footer) provided
directly by the program owner, adapted into WAF-safe HTML -- see
`_button` and `_wrap_branded` below for three real, confirmed-live content
restrictions on CreatorIQ's `sendBulk` endpoint that shaped this:

1. An inline `style="..."` attribute anywhere in the HTML silently 403s
   the *entire* request (a WAF sits in front of the endpoint -- it
   returns a generic HTML error page, not a JSON API error, so this is
   easy to misdiagnose as something else entirely).
2. Certain HTML tags are explicitly disallowed (`<small>` confirmed, via
   a real 422: "MessageContent has not allowed HTML tags: small").
3. Certain HTML attributes are explicitly disallowed too (`<font face="...">`
   confirmed, via a real 422: "MessageContent has not allowed HTML
   attributes: \"font.face\"" -- `<font color="...">` alone is fine).

All three were found by sending real test emails, not by reading
documentation (sendBulk's docs don't mention any of them). Given that,
this module deliberately sticks to old-school, attribute-based HTML
(`bgcolor`, `<font color>`, `cellpadding`) instead of modern inline CSS --
the "bulletproof button" pattern email developers used before CSS support
was reliable across clients, which happens to also be exactly what's safe
here -- and avoids custom fonts entirely (relies on each email client's
default font).

Each function returns `(subject, html_body)`. See
`src/fast_track/workflow/creator_emails.py` for exactly when each of
these gets sent.
"""

from __future__ import annotations

from datetime import date

from fast_track.config import CreatorEmailConfig

_BODY_TEXT_COLOR = "#333333"
_BRAND_PURPLE = "#7b189f"
_PAGE_BACKGROUND = "#f5f5f5"


def _button(text: str, url: str) -> str:
    """A "bulletproof" HTML-email button: `bgcolor` + `<font color>`, no

    `style="..."` anywhere (see module docstring for why that matters).
    """

    return (
        '<table cellspacing="0" cellpadding="14" align="center"><tr>'
        f'<td align="center" bgcolor="{_BRAND_PURPLE}">'
        f'<a href="{url}"><font color="#ffffff"><b>{text}</b></font></a>'
        "</td></tr></table>"
    )


def _wrap_branded(
    config: CreatorEmailConfig,
    body_html: str,
    button_text: str,
    button_url: str,
    today: date | None = None,
) -> str:
    """Wraps `body_html` in the shared logo header / white card / button /

    footer layout, using only WAF-safe attribute-based HTML (see module
    docstring). `config.logo_url` is optional -- left blank, the logo row
    is omitted entirely rather than showing a broken image (e.g. a Google
    Drive "view" share link won't resolve for recipients without access).
    """

    year = (today or date.today()).year
    logo_row = ""
    if config.logo_url:
        logo_row = (
            "<tr><td align=\"center\">"
            f'<a href="{config.creator_portal_url}">'
            f'<img src="{config.logo_url}" alt="Wayfair" height="50" border="0">'
            "</a></td></tr>"
        )
    button_row = f'<tr><td align="center">{_button(button_text, button_url)}</td></tr>'

    return (
        f'<table width="100%" cellspacing="0" cellpadding="0" bgcolor="{_PAGE_BACKGROUND}"><tr><td>'
        '<table width="600" cellspacing="0" cellpadding="10" align="center">'
        f"{logo_row}"
        "<tr><td>"
        f'<table width="100%" cellspacing="0" cellpadding="36" bgcolor="#ffffff"><tr><td>'
        f'<font color="{_BODY_TEXT_COLOR}">'
        f"{body_html}"
        "</font>"
        "</td></tr></table>"
        "</td></tr>"
        '<tr><td height="20">&nbsp;</td></tr>'
        f"{button_row}"
        '<tr><td height="20">&nbsp;</td></tr>'
        "</table>"
        "</td></tr></table>"
        '<p align="center"><font color="#888888">'
        f"Copyright &copy; {year} Wayfair. All rights reserved.</font></p>"
    )


def welcome_email(config: CreatorEmailConfig, today: date | None = None) -> tuple[str, str]:
    """Email 1: sent once, on day 0 (immediately upon joining)."""

    subject = "Welcome to the Wayfair Creator Program \u2014 Earn up to $50 \U0001f49c"
    body = f"""
<p>Hello \U0001f49c</p>
<p>Congratulations on joining the Wayfair Creator Program &mdash; we're so excited to have you!</p>
<p>To help you get started, you've unlocked an exclusive <strong>New Creator Bonus</strong> with
the opportunity to earn up to <strong>$50 in Wayfair gift card value</strong> during your first
14 days in the program.</p>
<p><strong>Here's how it works:</strong></p>
<p>\u2705 <strong>Post your first product link and/or storefront within your first 7 days</strong><br>
&rarr; Earn a <strong>$25 Wayfair Gift Card</strong></p>
<p>\u2705 <strong>Drive your first sale* within 14 days of joining</strong><br>
&rarr; Earn an additional <strong>$25 Wayfair Gift Card</strong></p>
<p><em>*Personal sales do not count toward the bonus</em></p>
<p>That's up to <strong>$50 in rewards</strong> just for getting started.</p>
<p>Getting started is easy:</p>
<ul>
<li>Share product links on Instagram, TikTok, Facebook, or anywhere your audience engages using
the tag #wayfaircreator, @wayfaircreators, #ad</li>
<li>Build your Wayfair storefront with collections you love</li>
<li>Earn 12% commission on qualifying sales through your links</li>
</ul>
<p>Need help setting up? Check out our
<a href="{config.getting_started_guide_url}">Getting Started Guide</a> and our
<a href="{config.posting_guide_url}">Posting Guide</a>.</p>
""".strip()
    full_body = (
        body
        + """
<p>As a reminder, active creators are also eligible for future gifting collaborations and paid
partnership opportunities.</p>
<p>To remain active in the program, complete at least one of the following each month:</p>
<ul>
<li>Share at least 3 qualifying links/posts</li>
<li>Update your Wayfair storefront with new collections or links</li>
</ul>
<p>If you have any questions, simply reply to this email or message us in your portal.</p>
<p>Follow @WayfairCreators for updates, inspiration, and creator opportunities \U0001f49c</p>
<p>The Wayfair Creator Team</p>
""".strip()
    )
    return subject, _wrap_branded(config, full_body, "VIEW DETAILS", config.creator_portal_url, today)


def sale_reminder_email(config: CreatorEmailConfig, today: date | None = None) -> tuple[str, str]:
    """Email 2: sent when a creator's first post is detected, then repeats

    (every `reminder_interval_days`) until they sell or the window closes.
    """

    subject = "You're one sale away from your next $25 \U0001f381"
    body = """
<p>Hello \U0001f49c</p>
<p>You've already completed your first post &mdash; amazing start!</p>
<p>Now you're just <strong>one sale away</strong> from unlocking your second:<br>
\U0001f381 $25 Wayfair Gift Card</p>
<p>You still have time to qualify within your first 14 days in the Wayfair Creator Program.</p>
<p>A few easy ways creators drive their first sale:</p>
<ul>
<li>Share your links across multiple platforms</li>
<li>Add products to your storefront collections</li>
<li>Re-share top-performing products to your audience</li>
<li>Use comment automators to send your links to your audience</li>
<li>Join the Boosting Program and use #wayfairelevate to get your content amplified</li>
</ul>
<p>Remember: you earn 12% commission on qualifying sales through your links, in addition to your
creator bonus rewards.</p>
<p>Access your creator portal below to keep sharing:</p>
<p>Cheering you on \U0001f49c</p>
<p>The Wayfair Creator Team</p>
""".strip()
    return subject, _wrap_branded(config, body, "CREATOR PORTAL", config.creator_portal_url, today)


def post_reminder_email(config: CreatorEmailConfig, today: date | None = None) -> tuple[str, str]:
    """Email 3: sent starting day 7 if a creator still hasn't posted, then

    repeats (every `reminder_interval_days`) until they post or the window
    closes.
    """

    subject = "Don't miss your $25 Wayfair Gift Card \U0001f49c"
    body = """
<p>Hello \U0001f49c</p>
<p>Just a reminder &mdash; you're still eligible to earn your first <strong>$25 Wayfair Gift
Card</strong> as part of our New Creator Bonus.</p>
<p>To unlock it, simply:<br>
\u2705 Post your first product link and/or storefront within your first 7 days in the
program.</p>
<p>Once your first post is live, you'll earn:<br>
\U0001f381 $25 Wayfair Gift Card</p>
<p>And if you drive your first sale within 14 days of joining, you'll unlock an additional:<br>
\U0001f381 $25 Wayfair Gift Card</p>
<p>Need inspiration? Start by:</p>
<ul>
<li>Sharing a favorite Wayfair product</li>
<li>Creating a quick roundup or collage</li>
<li>Building your storefront with products you already love</li>
</ul>
<p>Access your creator portal below</p>
<p>We can't wait to see what you create \U0001f49c</p>
<p>The Wayfair Creator Team</p>
""".strip()
    return subject, _wrap_branded(config, body, "CREATOR PORTAL", config.creator_portal_url, today)


def sale_congrats_email(config: CreatorEmailConfig, today: date | None = None) -> tuple[str, str]:
    """Email 4: sent once, immediately when a creator's first sale is detected."""

    subject = "Congrats on your first sale! \U0001f389"
    body = f"""
<p>Hello \U0001f49c</p>
<p>Congratulations &mdash; you officially made your first sale as a Wayfair Creator!
\U0001f389</p>
<p>Not only have you unlocked your additional <strong>$25 Wayfair Gift Card</strong>, but you've
also proven that your content can drive real engagement and purchases.</p>
<p>This is just the beginning \u2728</p>
<p>Creators who consistently share product links, update their storefronts, and stay active in
the program are the first to be considered for:</p>
<ul>
<li>Gifting collaborations</li>
<li>Paid partnership opportunities</li>
<li>Additional creator campaigns and bonuses</li>
</ul>
<p>Keep the momentum going by:</p>
<ul>
<li>Sharing your top-performing links again</li>
<li>Building out new storefront collections</li>
<li>Posting consistently across Instagram, TikTok, Facebook, and more</li>
</ul>
<p>Remember &mdash; you continue earning 12% commission on qualifying sales made through your
unique links and can level up in the
<a href="{config.creator_collective_url}">Creator Collective</a>.</p>
<p>Access your creator portal below to keep growing your storefront and earnings</p>
<p>We're so excited to continue building with you \U0001f49c</p>
<p>The Wayfair Creator Team</p>
""".strip()
    return subject, _wrap_branded(config, body, "CREATOR PORTAL", config.creator_portal_url, today)
