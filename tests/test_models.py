from datetime import date, datetime, timezone

from fast_track.models import ActivationRecord, ActivityRecord, Creator, Milestone


def make_creator(joined_at: str) -> Creator:
    return Creator.from_api(
        {"creator_id": "c-1", "name": "Test Creator", "email": "t@example.com", "joined_at": joined_at}
    )


def test_creator_from_api_parses_z_suffixed_datetime():
    creator = make_creator("2026-06-01T10:00:00Z")
    assert creator.joined_at == datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)


def test_cohort_week_start_defaults_to_monday():
    # 2026-06-03 is a Wednesday.
    creator = make_creator("2026-06-03T00:00:00Z")
    assert creator.cohort_week_start(week_start_weekday=1) == date(2026, 6, 1)


def test_cohort_week_start_on_the_boundary_day_itself():
    # 2026-06-01 is itself a Monday.
    creator = make_creator("2026-06-01T00:00:00Z")
    assert creator.cohort_week_start(week_start_weekday=1) == date(2026, 6, 1)


def test_cohort_week_start_supports_sunday_start():
    # 2026-06-03 is a Wednesday; week starting Sunday (2026-05-31).
    creator = make_creator("2026-06-03T00:00:00Z")
    assert creator.cohort_week_start(week_start_weekday=7) == date(2026, 5, 31)


def test_activation_record_completed_at():
    record = ActivationRecord.from_api(
        {"creator_id": "c-1", "first_post_at": "2026-06-02T00:00:00Z", "first_sale_at": None}
    )
    assert record.completed_at(Milestone.FIRST_POST) is not None
    assert record.completed_at(Milestone.FIRST_SALE) is None


def test_activity_record_is_active():
    active = ActivityRecord.from_api({"creator_id": "c-1", "date": "2026-06-02", "posts": 1})
    inactive = ActivityRecord.from_api({"creator_id": "c-1", "date": "2026-06-02"})
    assert active.is_active is True
    assert inactive.is_active is False
