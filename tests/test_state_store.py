from datetime import date

from fast_track.models import ActivityRecord, Creator, GiftAward, Milestone
from fast_track.storage.state_store import StateStore


def creator(cid: str) -> Creator:
    return Creator.from_api(
        {"creator_id": cid, "name": cid, "email": f"{cid}@example.com", "joined_at": "2026-06-01T00:00:00Z"}
    )


def award(cid: str, milestone: Milestone) -> GiftAward:
    return GiftAward(
        creator=creator(cid),
        milestone=milestone,
        amount_usd=25.0,
        completed_at=creator(cid).joined_at,
        cohort_week_start=date(2026, 6, 1),
    )


def test_upsert_and_get_creator(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator("c-1")])
        fetched = store.get_creator("c-1")
        assert fetched is not None
        assert fetched.email == "c-1@example.com"


def test_record_awards_is_idempotent(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator("c-1")])
        a = award("c-1", Milestone.FIRST_POST)
        store.record_awards([a])
        store.record_awards([a])  # re-recording should not error or duplicate
        awards = store.all_awards()
        assert len(awards) == 1


def test_filter_unrecorded_excludes_already_recorded(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator("c-1"), creator("c-2")])
        a1 = award("c-1", Milestone.FIRST_POST)
        a2 = award("c-2", Milestone.FIRST_POST)
        store.record_awards([a1])

        remaining = store.filter_unrecorded([a1, a2])
        assert [a.creator.creator_id for a in remaining] == ["c-2"]


def test_activity_upsert_overwrites_same_day(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        rec1 = ActivityRecord(creator_id="c-1", activity_date=date(2026, 6, 1), posts=1)
        rec2 = ActivityRecord(creator_id="c-1", activity_date=date(2026, 6, 1), posts=5)
        store.upsert_activity([rec1])
        store.upsert_activity([rec2])
        activity = store.get_activity(creator_ids=["c-1"])
        assert len(activity) == 1
        assert activity[0].posts == 5


def test_resolve_first_post_dates_records_date_on_first_observation(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        observed = store.resolve_first_post_dates({"c-1": 3, "c-2": 0}, today=date(2026, 7, 1))
        assert observed == {"c-1": date(2026, 7, 1)}


def test_resolve_first_post_dates_is_stable_across_calls(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        store.resolve_first_post_dates({"c-1": 1}, today=date(2026, 7, 1))
        # Even though the post count grew and "today" advanced, the
        # originally-observed date should stick.
        later = store.resolve_first_post_dates({"c-1": 5}, today=date(2026, 7, 10))
        assert later == {"c-1": date(2026, 7, 1)}


def test_resolve_first_post_dates_omits_creators_with_zero_count(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        observed = store.resolve_first_post_dates({"c-1": 0})
        assert observed == {}


def test_record_email_sent_and_last_email_sent_at(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        assert store.last_email_sent_at("c-1", "welcome") is None
        store.record_email_sent("c-1", "welcome", sent_at=date(2026, 7, 1))
        assert store.last_email_sent_at("c-1", "welcome") == date(2026, 7, 1)


def test_record_email_sent_updates_last_sent_and_count(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        store.record_email_sent("c-1", "post_reminder", sent_at=date(2026, 7, 1))
        store.record_email_sent("c-1", "post_reminder", sent_at=date(2026, 7, 3))
        assert store.last_email_sent_at("c-1", "post_reminder") == date(2026, 7, 3)
        row = store._conn.execute(
            "SELECT send_count FROM creator_emails WHERE creator_id = ? AND email_type = ?",
            ("c-1", "post_reminder"),
        ).fetchone()
        assert row["send_count"] == 2


def test_email_tracking_is_independent_per_email_type(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        store.record_email_sent("c-1", "welcome", sent_at=date(2026, 7, 1))
        assert store.last_email_sent_at("c-1", "post_reminder") is None
        assert store.last_email_sent_at("c-1", "welcome") == date(2026, 7, 1)


def test_get_activity_filters_by_date_range(tmp_path):
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_activity(
            [
                ActivityRecord(creator_id="c-1", activity_date=date(2026, 6, 1), posts=1),
                ActivityRecord(creator_id="c-1", activity_date=date(2026, 6, 15), posts=1),
                ActivityRecord(creator_id="c-1", activity_date=date(2026, 7, 1), posts=1),
            ]
        )
        activity = store.get_activity(start=date(2026, 6, 5), end=date(2026, 6, 20))
        assert [a.activity_date for a in activity] == [date(2026, 6, 15)]
