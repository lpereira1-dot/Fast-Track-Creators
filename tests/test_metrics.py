from datetime import date, datetime

from fast_track.dashboard.metrics import (
    build_daily_offsets,
    build_gift_events,
    retention_curve,
    summarize_retention,
)
from fast_track.models import ActivityRecord, Creator, GiftAward, Milestone


def _parse(iso_z: str) -> datetime:
    return datetime.fromisoformat(iso_z.replace("Z", "+00:00"))


def make_award(creator_id: str, milestone: Milestone, completed_at: str, joined_at: str) -> GiftAward:
    creator = Creator.from_api(
        {
            "creator_id": creator_id,
            "name": creator_id,
            "email": f"{creator_id}@example.com",
            "joined_at": joined_at,
        }
    )
    return GiftAward(
        creator=creator,
        milestone=milestone,
        amount_usd=25.0,
        completed_at=_parse(completed_at),
        cohort_week_start=date(2026, 6, 1),
    )


def test_build_gift_events_uses_earliest_milestone_as_gift_date():
    awards = [
        make_award("c-1", Milestone.FIRST_POST, "2026-06-03T00:00:00Z", "2026-06-01T00:00:00Z"),
        make_award("c-1", Milestone.FIRST_SALE, "2026-06-10T00:00:00Z", "2026-06-01T00:00:00Z"),
    ]
    events = build_gift_events(awards)
    assert len(events) == 1
    row = events.iloc[0]
    assert row["gift_date"] == date(2026, 6, 3)
    assert row["total_gift_usd"] == 50.0
    assert set(row["milestones"].split(", ")) == {"First Post", "First Sale"}


def test_build_daily_offsets_marks_active_days_and_drops_future_days():
    awards = [make_award("c-1", Milestone.FIRST_POST, "2026-06-03T00:00:00Z", "2026-06-01T00:00:00Z")]
    events = build_gift_events(awards)
    activity = [
        ActivityRecord(creator_id="c-1", activity_date=date(2026, 6, 2), posts=1),  # offset -1
        ActivityRecord(creator_id="c-1", activity_date=date(2026, 6, 4), sales=1),  # offset +1
    ]
    daily = build_daily_offsets(events, activity, window_days=5, as_of=date(2026, 6, 6))

    assert daily["offset"].min() == -5
    assert daily["offset"].max() == 3  # 2026-06-06 is offset +3 from 2026-06-03; later days dropped

    pre_day = daily[daily["date"] == date(2026, 6, 2)].iloc[0]
    assert pre_day["active"]
    no_activity_day = daily[daily["date"] == date(2026, 6, 1)].iloc[0]
    assert not no_activity_day["active"]


def test_retention_curve_aggregates_across_creators():
    awards = [
        make_award("c-1", Milestone.FIRST_POST, "2026-06-03T00:00:00Z", "2026-06-01T00:00:00Z"),
        make_award("c-2", Milestone.FIRST_POST, "2026-06-05T00:00:00Z", "2026-06-01T00:00:00Z"),
    ]
    events = build_gift_events(awards)
    activity = [
        ActivityRecord(creator_id="c-1", activity_date=date(2026, 6, 4), posts=1),  # c-1 offset +1
        ActivityRecord(creator_id="c-2", activity_date=date(2026, 6, 6), posts=1),  # c-2 offset +1
    ]
    daily = build_daily_offsets(events, activity, window_days=3, as_of=date(2026, 6, 10))
    curve = retention_curve(daily)

    offset_1 = curve[curve["offset"] == 1].iloc[0]
    assert offset_1["pct_active"] == 100.0
    assert offset_1["n_creators"] == 2


def test_summarize_retention_computes_lift_and_final_week_retention():
    awards = [make_award("c-1", Milestone.FIRST_POST, "2026-06-15T00:00:00Z", "2026-06-01T00:00:00Z")]
    events = build_gift_events(awards)
    activity = [
        # No activity pre-gift.
        ActivityRecord(creator_id="c-1", activity_date=date(2026, 6, 20), posts=1),  # offset +5
        ActivityRecord(creator_id="c-1", activity_date=date(2026, 6, 25), posts=1),  # offset +10, final week
    ]
    daily = build_daily_offsets(events, activity, window_days=10, as_of=date(2026, 7, 1))
    summary = summarize_retention(daily, window_days=10)

    assert summary["n_creators"] == 1
    assert summary["pre_active_rate"] == 0.0
    assert summary["post_active_rate"] > 0
    assert summary["final_week_retention_rate"] == 100.0


def test_empty_awards_produce_empty_summary():
    empty_events = build_gift_events([])
    daily = build_daily_offsets(empty_events, [], window_days=30)
    summary = summarize_retention(daily, window_days=30)
    assert summary["n_creators"] == 0
    assert summary["pre_active_rate"] is None
