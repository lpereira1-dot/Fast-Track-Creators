from datetime import date

from fast_track.api.creatoriq import FixtureCreatorIQClient
from fast_track.config import Settings
from fast_track.storage.state_store import StateStore
from fast_track.workflow.backfill import run_backfill_job


def test_backfill_populates_state_without_needing_a_sheet(tmp_path):
    settings = Settings()
    client = FixtureCreatorIQClient()

    with StateStore(tmp_path / "state.db") as store:
        result = run_backfill_job(
            client, store, settings, since=date(2026, 5, 1), until=date(2026, 8, 5)
        )
        assert result.creators_backfilled == 12
        assert result.awards_backfilled == 10
        assert len(store.all_creators()) == 12
        assert len(store.all_awards()) == 10


def test_backfill_is_safe_to_rerun(tmp_path):
    settings = Settings()
    client = FixtureCreatorIQClient()
    db_path = tmp_path / "state.db"

    with StateStore(db_path) as store:
        run_backfill_job(client, store, settings, since=date(2026, 5, 1), until=date(2026, 8, 5))
    with StateStore(db_path) as store:
        run_backfill_job(client, store, settings, since=date(2026, 5, 1), until=date(2026, 8, 5))
        assert len(store.all_awards()) == 10  # no duplicates
