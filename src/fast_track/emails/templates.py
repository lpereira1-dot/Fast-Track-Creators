"""HTML email templates for the Fast Track creator lifecycle reminders.

Copy provided directly by the program owner; only lightly reformatted into
HTML (paragraphs / lists / links) and parameterized for URLs and the
current year. Each function returns `(subject, html_body)`. See
`src/fast_track/workflow/creator_emails.py` for exactly when each of these
gets sent.
"""

from __future__ import annotations

from datetime import date

from fast_track.config import CreatorEmailConfig

_SIGNOFF = "<p>The Wayfair Creator Team</p>"


def _copyright_footer(today: date | None = None) -> str:
    # Deliberately plain -- no inline `style="..."` attribute (confirmed
    # live: CreatorIQ's sendBulk endpoint sits behind a WAF that silently
    # 403s the *entire* request over one, going by the generic HTML error
    # page rather than a JSON API error) and no `<small>` tag either
    # (confirmed live: sendBulk's own validation explicitly rejects it --
    # "MessageContent has not allowed HTML tags: small"). Just a plain
    # `<p>`, like every other paragraph in these templates.
    year = (today or date.today()).year
    return f"<p>Copyright &copy; {year} Wayfair. All rights reserved.</p>"


def welcome_email(config: CreatorEmailConfig) -> tuple[str, str]:
    """Email 1: sent once, on day 0 (immediately upon joining)."""

    subject = "Welcome to the Wayfair Creator Program \u2014 Earn up to $50 \U0001f49c"
    body = f"""
<p>Hello \U0001f49c</p>
<p>Congratulations on joining the Wayfair Creator Program &mdash; we're so excited to have you!</p>
<p>To help you get started, you've unlocked an exclusive <strong>New Creator Bonus</strong> with
the opportunity to earn up to <strong>$50 in Wayfair gift card value</strong> during your first
14 days in the program.</p>
<p>Here's how it works:</p>
<p>\u2705 Post your first product link and/or storefront within your first 7 days<br>
&rarr; Earn a <strong>$25 Wayfair Gift Card</strong></p>
<p>\u2705 Drive your first sale* within 14 days of joining<br>
&rarr; Earn an additional <strong>$25 Wayfair Gift Card</strong></p>
<p><em>*Personal sales do not count toward the bonus</em></p>
<p>That's up to $50 in rewards just for getting started.</p>
<p><strong>Getting started is easy:</strong></p>
<ul>
<li>Share product links on Instagram, TikTok, Facebook, or anywhere your audience engages using
the tag #wayfaircreator, @wayfaircreators, #ad</li>
<li>Build your Wayfair storefront with collections you love</li>
<li>Earn 12% commission on qualifying sales through your links</li>
</ul>
<p>Need help setting up? Check out our
<a href="{config.getting_started_guide_url}">Getting Started Guide</a> and our
<a href="{config.posting_guide_url}">Posting Guide</a>.</p>
<p><a href="{config.creator_portal_url}"><strong>VIEW DETAILS</strong></a></p>
<p>As a reminder, active creators are also eligible for future gifting collaborations and paid
partnership opportunities.</p>
<p>To remain active in the program, complete at least one of the following each month:</p>
<ul>
<li>Share at least 3 qualifying links/posts</li>
<li>Update your Wayfair storefront with new collections or links</li>
</ul>
<p>If you have any questions, simply reply to this email or message us in your portal.</p>
<p>Follow @WayfairCreators for updates, inspiration, and creator opportunities \U0001f49c</p>
{_SIGNOFF}
""".strip()
    return subject, body


def sale_reminder_email(config: CreatorEmailConfig) -> tuple[str, str]:
    """Email 2: sent when a creator's first post is detected, then repeats

    (every `reminder_interval_days`) until they sell or the window closes.
    """

    subject = "You're one sale away from your next $25 \U0001f381"
    body = f"""
<p>Hello \U0001f49c</p>
<p>You've already completed your first post &mdash; amazing start!</p>
<p>Now you're just one sale away from unlocking your second:<br>
\U0001f381 <strong>$25 Wayfair Gift Card</strong></p>
<p>You still have time to qualify within your first 14 days in the Wayfair Creator Program.</p>
<p><strong>A few easy ways creators drive their first sale:</strong></p>
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
<p><a href="{config.creator_portal_url}"><strong>CREATOR PORTAL</strong></a></p>
<p>Cheering you on \U0001f49c</p>
{_SIGNOFF}
""".strip()
    return subject, body


def post_reminder_email(config: CreatorEmailConfig, today: date | None = None) -> tuple[str, str]:
    """Email 3: sent starting day 7 if a creator still hasn't posted, then

    repeats (every `reminder_interval_days`) until they post or the window
    closes.
    """

    subject = "Don't miss your $25 Wayfair Gift Card \U0001f49c"
    body = f"""
<p>Hello \U0001f49c</p>
<p>Just a reminder &mdash; you're still eligible to earn your first $25 Wayfair Gift Card as part
of our New Creator Bonus.</p>
<p>To unlock it, simply:<br>
\u2705 Post your first product link and/or storefront within your first 7 days in the
program.</p>
<p>Once your first post is live, you'll earn:<br>
\U0001f381 <strong>$25 Wayfair Gift Card</strong></p>
<p>And if you drive your first sale within 14 days of joining, you'll unlock an additional:<br>
\U0001f381 <strong>$25 Wayfair Gift Card</strong></p>
<p><strong>Need inspiration? Start by:</strong></p>
<ul>
<li>Sharing a favorite Wayfair product</li>
<li>Creating a quick roundup or collage</li>
<li>Building your storefront with products you already love</li>
</ul>
<p>Access your creator portal below</p>
<p><a href="{config.creator_portal_url}"><strong>CREATOR PORTAL</strong></a></p>
<p>We can't wait to see what you create \U0001f49c</p>
{_SIGNOFF}
{_copyright_footer(today)}
""".strip()
    return subject, body


def sale_congrats_email(config: CreatorEmailConfig, today: date | None = None) -> tuple[str, str]:
    """Email 4: sent once, immediately when a creator's first sale is detected."""

    subject = "Congrats on your first sale! \U0001f389"
    body = f"""
<p>Hello \U0001f49c</p>
<p>Congratulations &mdash; you officially made your first sale as a Wayfair Creator!
\U0001f389</p>
<p>Not only have you unlocked your additional $25 Wayfair Gift Card, but you've also proven that
your content can drive real engagement and purchases.</p>
<p>This is just the beginning \u2728</p>
<p>Creators who consistently share product links, update their storefronts, and stay active in
the program are the first to be considered for:</p>
<ul>
<li>Gifting collaborations</li>
<li>Paid partnership opportunities</li>
<li>Additional creator campaigns and bonuses</li>
</ul>
<p><strong>Keep the momentum going by:</strong></p>
<ul>
<li>Sharing your top-performing links again</li>
<li>Building out new storefront collections</li>
<li>Posting consistently across Instagram, TikTok, Facebook, and more</li>
</ul>
<p>Remember &mdash; you continue earning 12% commission on qualifying sales made through your
unique links and can level up in the
<a href="{config.creator_collective_url}">Creator Collective</a>.</p>
<p>Access your creator portal below to keep growing your storefront and earnings</p>
<p><a href="{config.creator_portal_url}"><strong>CREATOR PORTAL</strong></a></p>
<p>We're so excited to continue building with you \U0001f49c</p>
{_SIGNOFF}
{_copyright_footer(today)}
""".strip()
    return subject, body
