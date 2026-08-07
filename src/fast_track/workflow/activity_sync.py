"""Keeps the local activity history (used by the retention dashboard) fresh.

Pulls daily activity for every creator we know about (i.e. everyone the
weekly cohort job has ever seen) across a window wide enough to cover the
pre/post retention analysis, and upserts it into the state store. Intended
to run daily so the dashboard's "days since gift" charts stay current.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from fast_track.api.creatoriq import ReportsClient
from fast_track.config import Settings
from fast_track.storage.state_store import StateStore


@dataclass
class ActivitySyncResult:
    creators_synced: int
    records_synced: int
    window_start: date
    window_end: date

    def summary(self) -> str:
        return (
            f"Synced {self.records_synced} activity record(s) for {self.creators_synced} "
            f"creator(s) between {self.window_start} and {self.window_end}."
        )


def run_activity_sync_job(
    reports_client: ReportsClient,
    store: StateStore,
    settings: Settings,
    today: date | None = None,
) -> ActivitySyncResult:
    today = today or date.today()
    creators = store.all_creators()
    if not creators:
        return ActivitySyncResult(0, 0, today, today)

    earliest_joined = min(c.joined_at.date() for c in creators)
    window_start = earliest_joined - timedelta(days=settings.program.retention_window_days)

    records = reports_client.fetch_activity(
        [c.creator_id for c in creators], window_start, today
    )
    store.upsert_activity(records)

    return ActivitySyncResult(
        creators_synced=len(creators),
        records_synced=len(records),
        window_start=window_start,
        window_end=today,
    )
