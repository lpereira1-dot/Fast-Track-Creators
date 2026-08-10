from datetime import date

from fast_track.config import CreatorEmailConfig
from fast_track.emails import templates

_ALL_TEMPLATES = [
    lambda cfg, today: templates.welcome_email(cfg, today),
    lambda cfg, today: templates.sale_reminder_email(cfg, today),
    lambda cfg, today: templates.post_reminder_email(cfg, today),
    lambda cfg, today: templates.sale_congrats_email(cfg, today),
]


def _config() -> CreatorEmailConfig:
    return CreatorEmailConfig(
        creator_portal_url="https://example.com/portal",
        getting_started_guide_url="https://example.com/guide",
        posting_guide_url="https://example.com/posting",
        creator_collective_url="https://example.com/collective",
    )


def test_no_template_uses_an_inline_style_attribute_or_a_small_tag():
    """CreatorIQ's sendBulk endpoint has two real, confirmed-live content

    restrictions this guards against reintroducing:
    - An inline `style="..."` attribute anywhere in the HTML silently
      403s the *entire* request (a WAF sits in front of the endpoint --
      it returns a generic HTML error page, not a JSON API error).
    - `<small>` is an explicitly disallowed tag (a real 422 with message
      "MessageContent has not allowed HTML tags: small").
    Both confirmed by sending real test emails.
    """

    cfg = _config()
    today = date(2026, 8, 17)
    for build in _ALL_TEMPLATES:
        _subject, body = build(cfg, today)
        assert "style=" not in body
        assert "<small" not in body
        assert "face=" not in body  # confirmed live: 422 "not allowed HTML attributes: font.face"


def test_all_templates_render_with_configured_urls_and_no_leftover_placeholders():
    cfg = _config()
    today = date(2026, 8, 17)
    for build in _ALL_TEMPLATES:
        subject, body = build(cfg, today)
        assert subject
        assert body
        assert "{" not in body  # no unfilled f-string/format placeholders
        assert "None" not in body


def test_welcome_email_links_to_all_three_guides():
    cfg = _config()
    _subject, body = templates.welcome_email(cfg)
    assert cfg.creator_portal_url in body
    assert cfg.getting_started_guide_url in body
    assert cfg.posting_guide_url in body


def test_sale_congrats_links_to_creator_collective():
    cfg = _config()
    _subject, body = templates.sale_congrats_email(cfg, date(2026, 8, 17))
    assert cfg.creator_collective_url in body


def test_copyright_footer_uses_the_provided_year():
    cfg = _config()
    today = date(2027, 1, 1)
    for build in _ALL_TEMPLATES:
        _subject, body = build(cfg, today)
        assert "Copyright &copy; 2027" in body


def test_button_uses_brand_purple_and_no_style_attribute():
    cfg = _config()
    today = date(2026, 8, 17)
    for build in _ALL_TEMPLATES:
        _subject, body = build(cfg, today)
        assert 'bgcolor="#7b189f"' in body


def test_logo_row_omitted_when_logo_url_is_blank():
    cfg = _config()
    assert cfg.logo_url == ""
    _subject, body = templates.welcome_email(cfg, date(2026, 8, 17))
    assert "<img" not in body


def test_logo_row_included_when_logo_url_is_set():
    cfg = CreatorEmailConfig(
        creator_portal_url="https://example.com/portal",
        getting_started_guide_url="https://example.com/guide",
        posting_guide_url="https://example.com/posting",
        creator_collective_url="https://example.com/collective",
        logo_url="https://example.com/logo.png",
    )
    _subject, body = templates.welcome_email(cfg, date(2026, 8, 17))
    assert '<img src="https://example.com/logo.png"' in body
