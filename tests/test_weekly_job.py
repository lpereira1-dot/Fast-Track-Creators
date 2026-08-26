from datetime import date, datetime

from fast_track.api.creatoriq import FixtureCreatorIQClient
from dataclasses import replace

from fast_track.config import ProgramRules, Settings
from fast_track.models import ActivationRecord, Creator, GiftAward
from fast_track.storage.state_store import StateStore
from fast_track.workflow.weekly_job import run_weekly_cohort_job


class SingleCreatorReportsClient:
    """Fake ReportsClient with one creator who qualifies for both milestones."""

    def __init__(self, creator: Creator, activation: ActivationRecord):
        self._creator = creator
        self._activation = activation

    def fetch_new_creators(self, since, until):
        return [self._creator] if since <= self._creator.joined_at.date() <= until else []

    def fetch_activation(self, creator_ids):
        return [self._activation] if self._creator.creator_id in creator_ids else []

    def fetch_activity(self, creator_ids, start, end):
        return []

    def fetch_creator_email(self, creator_id):
        return self._creator.email


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


def test_weekly_job_awards_both_milestones_as_separate_fifty_dollar_total(tmp_path):
    """A creator who both posts and sells within the window earns $50 total

    (two independent $25 awards), not a single $25 -- confirms the
    dedup-by-(creator_id, milestone) key never collapses the two into one.
    """

    settings = Settings()
    creator = Creator.from_api(
        {
            "creator_id": "c-both",
            "name": "Both Milestones",
            "email": "both@example.com",
            "joined_at": "2026-07-25T00:00:00Z",
        }
    )
    activation = ActivationRecord.from_api(
        {
            "creator_id": "c-both",
            "first_post_at": "2026-07-26T00:00:00Z",
            "first_sale_at": "2026-08-01T00:00:00Z",
        }
    )
    client = SingleCreatorReportsClient(creator, activation)
    sheet = FakeSheetClient()

    with StateStore(tmp_path / "state.db") as store:
        result = run_weekly_cohort_job(
            client, store, settings, sheet_client=sheet, today=date(2026, 8, 5)
        )

    assert len(result.newly_added_awards) == 2
    assert sum(a.amount_usd for a in result.newly_added_awards) == 50.0
    assert {a.milestone.label for a in result.newly_added_awards} == {"First Post", "First Sale"}
    # Both landed as distinct sheet rows -- neither treated as a duplicate of the other.
    assert len(sheet.rows) == 2
    assert ("c-both", "First Post") in sheet.rows
    assert ("c-both", "First Sale") in sheet.rows


def test_weekly_job_self_heals_local_state_behind_the_sheet(tmp_path):
    """If local state is ever behind the sheet's true content (e.g. after a

    state-loss incident -- this guards against a real bug where a broken
    GitHub Actions cache silently reset local history while the sheet
    itself stayed correct), a run should catch local state back up to
    reality, not just record whatever the sheet-level dedup decided was
    new to *write* this run.
    """

    settings = Settings()
    client = FixtureCreatorIQClient()
    sheet = FakeSheetClient()
    # Simulate the sheet already having one of the two awards from a
    # previous run whose local record was lost (c-1009's First Post).
    sheet.rows.append(("c-1009", "First Post"))

    with StateStore(tmp_path / "state.db") as store:
        result = run_weekly_cohort_job(
            client, store, settings, sheet_client=sheet, today=date(2026, 8, 5)
        )
        # Only the genuinely-new one gets (re-)written to the sheet.
        assert {a.creator.creator_id for a in result.newly_added_awards} == {"c-1012"}
        assert len(sheet.rows) == 2  # unchanged count -- no duplicate for c-1009
        # But local state reflects BOTH as recorded, not just the one that
        # was newly appended to the sheet this run.
        recorded_ids = {a.creator.creator_id for a in store.all_awards()}
        assert recorded_ids == {"c-1009", "c-1012"}


def test_weekly_job_excludes_creators_before_min_join_date(tmp_path):
    settings = Settings(program=replace(ProgramRules(), min_join_date=date(2026, 8, 17)))
    pre_launch = Creator.from_api(
        {
            "creator_id": "c-old",
            "name": "Pre Launch",
            "email": "old@example.com",
            "joined_at": "2026-08-10T00:00:00Z",
        }
    )
    activation = ActivationRecord.from_api(
        {
            "creator_id": "c-old",
            "first_post_at": "2026-08-11T00:00:00Z",
            "first_sale_at": None,
        }
    )
    client = SingleCreatorReportsClient(pre_launch, activation)
    sheet = FakeSheetClient()

    with StateStore(tmp_path / "state.db") as store:
        result = run_weekly_cohort_job(
            client, store, settings, sheet_client=sheet, today=date(2026, 8, 25)
        )
        assert result.newly_added_awards == []
        assert sheet.rows == []
        assert store.all_awards() == []


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
