from datetime import date, datetime

from fast_track.api.creatoriq import FixtureCreatorIQClient
from fast_track.config import Settings
from fast_track.models import GiftAward
from fast_track.storage.state_store import StateStore
from fast_track.workflow.weekly_job import run_weekly_cohort_job


class FakeSheetClient:
    """In-memory stand-in for GiftOrderSheetClient, mirroring its dedup semantics."""

    def __init__(self):
        self.rows: list[tuple[str, str]] = []

    def append_awards(self, awards: list[GiftAward], added_at: datetime | None = None) -> list[GiftAward]:
        appended = []
        for award in awards:
            key = (award.creator.creator_id, award.milestone.label)
            if key not in self.rows:
                self.rows.append(key)
                appended.append(award)
        return appended


def test_weekly_job_adds_newly_qualified_creators(tmp_path):
    settings = Settings()
    client = FixtureCreatorIQClient()
    sheet = FakeSheetClient()

    with StateStore(tmp_path / "state.db") as store:
        result = run_weekly_cohort_job(
            client, store, settings, sheet_client=sheet, today=date(2026, 8, 5)
        )

    assert result.creators_checked == 4  # fixture creators who joined in the 21-day lookback
    added_ids = {a.creator.creator_id for a in result.newly_added_awards}
    assert added_ids == {"c-1009", "c-1012"}
    assert len(sheet.rows) == 2


def test_weekly_job_is_idempotent_across_reruns(tmp_path):
    settings = Settings()
    client = FixtureCreatorIQClient()
    sheet = FakeSheetClient()
    db_path = tmp_path / "state.db"

    with StateStore(db_path) as store:
        run_weekly_cohort_job(client, store, settings, sheet_client=sheet, today=date(2026, 8, 5))

    # Re-run the following week: no new activation data, so nothing new should be added.
    with StateStore(db_path) as store:
        result = run_weekly_cohort_job(
            client, store, settings, sheet_client=sheet, today=date(2026, 8, 12)
        )

    assert result.newly_added_awards == []
    assert len(sheet.rows) == 2  # unchanged from the first run


def test_weekly_job_dry_run_does_not_touch_sheet_or_state(tmp_path):
    settings = Settings()
    client = FixtureCreatorIQClient()
    sheet = FakeSheetClient()

    with StateStore(tmp_path / "state.db") as store:
        result = run_weekly_cohort_job(
            client, store, settings, sheet_client=sheet, today=date(2026, 8, 5), dry_run=True
        )
        assert result.dry_run is True
        assert len(result.newly_added_awards) == 2
        assert sheet.rows == []  # dry run never calls the sheet
        assert store.all_awards() == []  # dry run never persists state
