from datetime import date, datetime, timedelta

from fast_track.dashboard.metrics import (
    build_daily_offsets,
    build_gift_events,
    merge_activity_records,
    milestone_activity_records,
    retention_curve,
    summarize_retention,
)
from fast_track.models import ActivityRecord, Creator, GiftAward, Milestone


def _parse(iso_z: str) -> datetime:
    return datetime.fromisoformat(iso_z.replace("Z", "+00:00"))


def make_award(
    creator_id: str,
    milestone: Milestone,
    completed_at: str,
    joined_at: str,
    added_at: str | None = None,
) -> GiftAward:
    creator = Creator.from_api(
        {
            "creator_id": creator_id,
            "name": creator_id,
            "email": f"{creator_id}@example.com",
            "joined_at": joined_at,
        }
    )
    completed = _parse(completed_at)
    return GiftAward(
        creator=creator,
        milestone=milestone,
        amount_usd=25.0,
        completed_at=completed,
        cohort_week_start=date(2026, 6, 1),
        added_at=_parse(added_at) if added_at else completed + timedelta(days=7),
    )


def test_build_gift_events_uses_earliest_sheet_date_as_gift_date():
    awards = [
        make_award(
            "c-1",
            Milestone.FIRST_POST,
            "2026-06-03T00:00:00Z",
            "2026-06-01T00:00:00Z",
            added_at="2026-06-05T00:00:00Z",
        ),
        make_award(
            "c-1",
            Milestone.FIRST_SALE,
            "2026-06-10T00:00:00Z",
            "2026-06-01T00:00:00Z",
            added_at="2026-06-12T00:00:00Z",
        ),
    ]
    events = build_gift_events(awards)
    assert len(events) == 1
    row = events.iloc[0]
    assert row["gift_date"] == date(2026, 6, 5)
    assert row["total_gift_usd"] == 50.0
    assert set(row["milestones"].split(", ")) == {"First Post", "First Sale"}


def test_build_daily_offsets_marks_active_days_and_drops_future_days():
    awards = [
        make_award(
            "c-1",
            Milestone.FIRST_POST,
            "2026-06-03T00:00:00Z",
            "2026-06-01T00:00:00Z",
            added_at="2026-06-03T00:00:00Z",
        )
    ]
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
        make_award(
            "c-1",
            Milestone.FIRST_POST,
            "2026-06-03T00:00:00Z",
            "2026-06-01T00:00:00Z",
            added_at="2026-06-03T00:00:00Z",
        ),
        make_award(
            "c-2",
            Milestone.FIRST_POST,
            "2026-06-05T00:00:00Z",
            "2026-06-01T00:00:00Z",
            added_at="2026-06-05T00:00:00Z",
        ),
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
    awards = [
        make_award(
            "c-1",
            Milestone.FIRST_POST,
            "2026-06-15T00:00:00Z",
            "2026-06-01T00:00:00Z",
            added_at="2026-06-15T00:00:00Z",
        )
    ]
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


def test_milestone_activity_records_marks_first_post_and_sale_days():
    awards = [
        make_award("c-1", Milestone.FIRST_POST, "2026-06-03T00:00:00Z", "2026-06-01T00:00:00Z"),
        make_award("c-1", Milestone.FIRST_SALE, "2026-06-10T00:00:00Z", "2026-06-01T00:00:00Z"),
    ]
    records = milestone_activity_records(awards)
    assert len(records) == 2
    post_day = next(r for r in records if r.posts)
    sale_day = next(r for r in records if r.sales)
    assert post_day.activity_date == date(2026, 6, 3)
    assert sale_day.activity_date == date(2026, 6, 10)


def test_first_post_counts_in_pre_period_when_gift_sheeted_later():
    awards = [
        make_award(
            "c-1",
            Milestone.FIRST_POST,
            "2026-06-03T00:00:00Z",
            "2026-06-01T00:00:00Z",
            added_at="2026-06-10T00:00:00Z",
        ),
    ]
    events = build_gift_events(awards)
    retention_activity = merge_activity_records(milestone_activity_records(awards))
    daily = build_daily_offsets(events, retention_activity, window_days=10, as_of=date(2026, 6, 20))

    assert daily[daily["period"] == "pre"]["posts"].sum() == 1
    assert daily[daily["period"] == "gift_day"]["posts"].sum() == 0
    assert daily[daily["period"] == "post"]["posts"].sum() == 0


def test_retention_uses_milestone_activity_when_transaction_sync_is_empty():
    awards = [
        make_award(
            "c-1",
            Milestone.FIRST_POST,
            "2026-06-03T00:00:00Z",
            "2026-06-01T00:00:00Z",
            added_at="2026-06-10T00:00:00Z",
        ),
        make_award(
            "c-1",
            Milestone.FIRST_SALE,
            "2026-06-08T00:00:00Z",
            "2026-06-01T00:00:00Z",
            added_at="2026-06-12T00:00:00Z",
        ),
    ]
    events = build_gift_events(awards)
    retention_activity = merge_activity_records(milestone_activity_records(awards))
    daily = build_daily_offsets(events, retention_activity, window_days=10, as_of=date(2026, 6, 20))

    assert daily[daily["period"] == "pre"]["posts"].sum() == 1
    assert daily[daily["period"] == "pre"]["sales"].sum() == 1
    sale_day = daily[daily["date"] == date(2026, 6, 8)].iloc[0]
    assert sale_day["period"] == "pre"
    assert sale_day["sales"] == 1
    assert sale_day["active"]
