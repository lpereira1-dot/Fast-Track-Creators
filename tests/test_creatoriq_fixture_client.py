from datetime import date

from fast_track.api.creatoriq import FixtureCreatorIQClient


def test_fetch_new_creators_filters_by_join_window():
    client = FixtureCreatorIQClient()
    creators = client.fetch_new_creators(since=date(2026, 7, 1), until=date(2026, 8, 5))
    ids = {c.creator_id for c in creators}
    assert "c-1009" in ids  # joined 2026-07-27
    assert "c-1012" in ids  # joined 2026-08-03
    assert "c-1001" not in ids  # joined 2026-06-01, outside window


def test_fetch_activation_returns_only_requested_creators():
    client = FixtureCreatorIQClient()
    records = client.fetch_activation(["c-1001", "c-1004"])
    by_id = {r.creator_id: r for r in records}
    assert set(by_id) == {"c-1001", "c-1004"}
    assert by_id["c-1001"].first_post_at is not None
    assert by_id["c-1004"].first_post_at is None


def test_fetch_activity_filters_by_creator_and_date():
    client = FixtureCreatorIQClient()
    records = client.fetch_activity(["c-1001"], date(2026, 6, 1), date(2026, 6, 30))
    assert all(r.creator_id == "c-1001" for r in records)
    assert all(date(2026, 6, 1) <= r.activity_date <= date(2026, 6, 30) for r in records)
    assert len(records) > 0


def test_fetch_creator_email_looks_up_from_fixture():
    client = FixtureCreatorIQClient()
    assert client.fetch_creator_email("c-1001") == "ava.thompson@example.com"


def test_fetch_creator_email_returns_empty_for_unknown_creator():
    client = FixtureCreatorIQClient()
    assert client.fetch_creator_email("no-such-id") == ""


def test_send_bulk_email_does_not_raise_in_fixture_mode():
    client = FixtureCreatorIQClient()
    # Just a no-op logger in demo/fixture mode -- should never raise.
    client.send_bulk_email("Subject", "<p>Body</p>", ["c-1001"])
    client.send_bulk_email("Subject", "<p>Body</p>", [])
