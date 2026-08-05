"""Weekly job: pull new-creator cohorts from CreatorIQ, evaluate gift-card
eligibility, and sync newly-qualified creators into the ordering team's
Google Sheet.

Designed to run on a schedule (see .github/workflows/weekly-gift-cohort.yml)
but is idempotent and safe to re-run manually/backfill: creators are
re-checked every run within a rolling lookback window (since a creator might
hit their first-sale milestone in a later week than their first-post
milestone), and a milestone is only ever written to the sheet once thanks to
the local state store + a defense-in-depth check against the sheet itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from fast_track.api.creatoriq import ReportsClient
from fast_track.config import Settings
from fast_track.models import Creator, GiftAward
from fast_track.sheets.gift_order_sheet import GiftOrderSheetClient
from fast_track.storage.state_store import StateStore
from fast_track.workflow.cohorts import group_into_weekly_cohorts
from fast_track.workflow.eligibility import evaluate_all_awards

logger = logging.getLogger(__name__)


@dataclass
class WeeklyJobResult:
    window_start: date
    window_end: date
    creators_checked: int
    cohorts: dict[date, list[Creator]] = field(default_factory=dict)
    qualifying_awards: list[GiftAward] = field(default_factory=list)
    newly_added_awards: list[GiftAward] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        lines = [
            f"Checked {self.creators_checked} creator(s) who joined between "
            f"{self.window_start} and {self.window_end}, across {len(self.cohorts)} weekly cohort(s).",
            f"{len(self.qualifying_awards)} milestone(s) currently qualify for a gift card.",
        ]
        if self.dry_run:
            lines.append(
                f"[dry run] {len(self.newly_added_awards)} new milestone(s) would be added to the sheet."
            )
        else:
            lines.append(
                f"{len(self.newly_added_awards)} new milestone(s) added to the gift-card ordering sheet."
            )
        for award in self.newly_added_awards:
            lines.append(
                f"  - {award.creator.name} <{award.creator.email}>: "
                f"{award.milestone.label} (${award.amount_usd:.0f}) on {award.completed_at.date()}"
            )
        return "\n".join(lines)


def lookback_days(settings: Settings) -> int:
    """How far back to look for 'new' creators each run.

    A full activation window plus an extra week of buffer ensures a creator
    who, say, posts on day 13 and is checked again the following week still
    gets re-evaluated (rather than only ever being considered in the single
    week they joined).
    """

    return settings.program.activation_window_days + 7


def run_weekly_cohort_job(
    reports_client: ReportsClient,
    store: StateStore,
    settings: Settings,
    sheet_client: GiftOrderSheetClient | None = None,
    today: date | None = None,
    dry_run: bool = False,
) -> WeeklyJobResult:
    today = today or date.today()
    window_start = today - timedelta(days=lookback_days(settings))

    creators = reports_client.fetch_new_creators(since=window_start, until=today)
    store.upsert_creators(creators)

    activation_records = reports_client.fetch_activation([c.creator_id for c in creators])
    activations_by_id = {r.creator_id: r for r in activation_records}

    awards = evaluate_all_awards(creators, activations_by_id, settings.program)
    new_awards = store.filter_unrecorded(awards)

    if dry_run or sheet_client is None:
        newly_added = new_awards
        if sheet_client is None and not dry_run:
            logger.warning(
                "No Google Sheets client configured; running in dry-run mode. "
                "Set GIFT_ORDER_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON to sync for real."
            )
    else:
        newly_added = sheet_client.append_awards(new_awards)
        store.record_awards(newly_added)

    return WeeklyJobResult(
        window_start=window_start,
        window_end=today,
        creators_checked=len(creators),
        cohorts=group_into_weekly_cohorts(creators, settings.program),
        qualifying_awards=awards,
        newly_added_awards=newly_added,
        dry_run=dry_run or sheet_client is None,
    )
