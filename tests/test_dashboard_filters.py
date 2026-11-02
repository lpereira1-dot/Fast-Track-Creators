from datetime import date

from fast_track.dashboard.filters import (
    cohort_week_start,
    creator_ids_for_admit_month,
    creator_ids_for_admit_week,
    joined_month_options,
)
from fast_track.models import Creator


def _creator(cid: str, joined: str) -> Creator:
    return Creator.from_api(
        {
            "creator_id": cid,
            "name": cid,
            "email": f"{cid}@example.com",
            "joined_at": joined,
        }
    )


def test_creator_ids_for_admit_week():
    creators = [
        _creator("c-1", "2026-08-18T00:00:00Z"),
        _creator("c-2", "2026-08-19T00:00:00Z"),
        _creator("c-3", "2026-08-25T00:00:00Z"),
    ]
    by_week = {
        date(2026, 8, 17): creators[:2],
        date(2026, 8, 24): [creators[2]],
    }
    ids, week = creator_ids_for_admit_week(date(2026, 8, 19), by_week, week_start_weekday=1)
    assert week == date(2026, 8, 17)
    assert ids == {"c-1", "c-2"}


def test_creator_ids_for_admit_month():
    creators = [
        _creator("c-1", "2026-08-05T00:00:00Z"),
        _creator("c-2", "2026-08-28T00:00:00Z"),
        _creator("c-3", "2026-09-02T00:00:00Z"),
    ]
    ids = creator_ids_for_admit_month(date(2026, 8, 1), creators)
    assert ids == {"c-1", "c-2"}


def test_joined_month_options_newest_first():
    creators = [
        _creator("c-1", "2026-07-15T00:00:00Z"),
        _creator("c-2", "2026-08-03T00:00:00Z"),
    ]
    options = joined_month_options(creators)
    assert [label for _, label in options] == ["August 2026", "July 2026"]
