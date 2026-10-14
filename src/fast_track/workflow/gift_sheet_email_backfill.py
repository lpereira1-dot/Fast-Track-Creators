"""Repair Email cells on the gift-card ordering sheet.

Rows appended before email validation shipped may have blank values,
handles, or truncated roster strings. This job re-fetches from CreatorIQ
and updates cells in place (it does not add new rows).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from fast_track.api.creatoriq import ReportsClient
from fast_track.email_utils import is_valid_email
from fast_track.sheets.gift_order_sheet import GiftOrderSheetClient

logger = logging.getLogger(__name__)


@dataclass
class GiftSheetEmailBackfillResult:
    rows_scanned: int = 0
    rows_updated: int = 0
    rows_still_missing: int = 0
    dry_run: bool = False
    updated_creator_ids: list[str] = field(default_factory=list)

    def summary(self) -> str:
        prefix = "[dry run] " if self.dry_run else ""
        lines = [
            f"{prefix}Scanned {self.rows_scanned} gift-sheet row(s).",
            f"{prefix}Updated {self.rows_updated} email cell(s).",
        ]
        if self.rows_still_missing:
            lines.append(
                f"{self.rows_still_missing} row(s) still lack a valid email after CreatorIQ lookup."
            )
        return "\n".join(lines)


def run_gift_sheet_email_backfill(
    reports_client: ReportsClient,
    sheet_client: GiftOrderSheetClient,
    dry_run: bool = False,
) -> GiftSheetEmailBackfillResult:
    rows = sheet_client.rows_missing_valid_email()
    result = GiftSheetEmailBackfillResult(rows_scanned=len(rows), dry_run=dry_run)
    updates: list[tuple[int, str, str]] = []
    for row_number, creator_id, _current in rows:
        email = reports_client.fetch_creator_email(creator_id)
        if not is_valid_email(email):
            result.rows_still_missing += 1
            logger.warning(
                "Could not resolve email for creator %s (sheet row %d)", creator_id, row_number
            )
            continue
        updates.append((row_number, creator_id, email))
    if dry_run:
        result.rows_updated = len(updates)
        result.updated_creator_ids = [cid for _, cid, _ in updates]
        return result
    applied = sheet_client.apply_email_updates(updates)
    result.rows_updated = applied
    result.updated_creator_ids = [cid for _, cid, _ in updates[:applied]]
    return result
