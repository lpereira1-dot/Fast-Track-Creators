"""One-time backfill for creators/awards from before this automated workflow existed.

The gift-card test already ran manually for its first cohorts (gift cards
were hand-ordered from daily-pulled reports), so those milestones should
NOT be re-sent to the ordering sheet. This job only populates local state
(creators + awards) so the retention dashboard can include that historical
data; it never touches the Google Sheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fast_track.api.creatoriq import ReportsClient
from fast_track.config import Settings
from fast_track.storage.state_store import StateStore
from fast_track.workflow.eligibility import evaluate_all_awards


@dataclass
class BackfillResult:
    window_start: date
    window_end: date
    creators_backfilled: int
    awards_backfilled: int

    def summary(self) -> str:
        return (
            f"Backfilled {self.creators_backfilled} creator(s) and {self.awards_backfilled} "
            f"award(s) for the window {self.window_start} to {self.window_end} "
            "(dashboard/history only -- no sheet rows written)."
        )


def run_backfill_job(
    reports_client: ReportsClient,
    store: StateStore,
    settings: Settings,
    since: date,
    until: date,
) -> BackfillResult:
    creators = reports_client.fetch_new_creators(since=since, until=until)
    store.upsert_creators(creators)

    activation_records = reports_client.fetch_activation([c.creator_id for c in creators])
    activations_by_id = {r.creator_id: r for r in activation_records}

    awards = evaluate_all_awards(creators, activations_by_id, settings.program)
    store.record_awards(awards)

    return BackfillResult(
        window_start=since,
        window_end=until,
        creators_backfilled=len(creators),
        awards_backfilled=len(awards),
    )
