from datetime import date

from fast_track.config import ProgramRules
from fast_track.models import Creator
from fast_track.workflow.cohorts import group_into_weekly_cohorts

RULES = ProgramRules(cohort_week_start_weekday=1)


def creator(cid: str, joined: str) -> Creator:
    return Creator.from_api(
        {"creator_id": cid, "name": cid, "email": f"{cid}@example.com", "joined_at": joined}
    )


def test_groups_creators_by_monday_week_start():
    creators = [
        creator("c-1", "2026-06-01T00:00:00Z"),  # Monday
        creator("c-2", "2026-06-03T00:00:00Z"),  # Wednesday, same week
        creator("c-3", "2026-06-08T00:00:00Z"),  # Next Monday
    ]
    cohorts = group_into_weekly_cohorts(creators, RULES)
    assert list(cohorts.keys()) == [date(2026, 6, 1), date(2026, 6, 8)]
    assert {c.creator_id for c in cohorts[date(2026, 6, 1)]} == {"c-1", "c-2"}
    assert {c.creator_id for c in cohorts[date(2026, 6, 8)]} == {"c-3"}


def test_empty_input_returns_empty_dict():
    assert group_into_weekly_cohorts([], RULES) == {}
