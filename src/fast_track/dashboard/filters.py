"""Admission-date filters for the Streamlit dashboard."""

from __future__ import annotations

from datetime import date, timedelta

from fast_track.models import Creator


def cohort_week_start(joined: date, week_start_weekday: int) -> date:
    target = week_start_weekday - 1
    delta_days = (joined.weekday() - target) % 7
    return joined - timedelta(days=delta_days)


def joined_month_options(creators: list[Creator]) -> list[tuple[date, str]]:
    """Distinct calendar months (by join date), newest first."""

    months: set[tuple[int, int]] = set()
    for creator in creators:
        joined = creator.joined_at.date()
        months.add((joined.year, joined.month))
    return [
        (date(year, month, 1), date(year, month, 1).strftime("%B %Y"))
        for year, month in sorted(months, reverse=True)
    ]


def creator_ids_for_admit_week(
    pick: date,
    cohorts_by_week: dict[date, list[Creator]],
    week_start_weekday: int,
) -> tuple[set[str], date]:
    week_start = cohort_week_start(pick, week_start_weekday)
    creators = cohorts_by_week.get(week_start, [])
    return {c.creator_id for c in creators}, week_start


def creator_ids_for_admit_month(
    month_start: date,
    creators: list[Creator],
) -> set[str]:
    return {
        c.creator_id
        for c in creators
        if c.joined_at.year == month_start.year and c.joined_at.month == month_start.month
    }
