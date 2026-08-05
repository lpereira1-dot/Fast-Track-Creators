"""Syncs newly-qualified gift-card recipients into the ordering team's Google Sheet.

The ordering team already has a live Google Sheet they use to place gift
card orders; we only ever append rows to it (never overwrite/reformat their
existing columns/formulas), and we dedupe by (Creator ID, Milestone) both
against local state (see storage/state_store.py) and against whatever is
already in the sheet, so re-running the job is always safe.
"""

from __future__ import annotations

from datetime import datetime, timezone

import gspread

from fast_track.config import GoogleSheetsConfig
from fast_track.models import GiftAward


class GiftOrderSheetClient:
    def __init__(self, config: GoogleSheetsConfig, gspread_client: gspread.Client | None = None):
        if not config.has_credentials():
            raise ValueError(
                "Google Sheets credentials are missing. Set GIFT_ORDER_SHEET_ID and "
                "GOOGLE_SERVICE_ACCOUNT_JSON."
            )
        self._config = config
        self._client = gspread_client or gspread.service_account(
            filename=config.service_account_json_path
        )
        self._worksheet = self._open_worksheet()

    def _open_worksheet(self) -> gspread.Worksheet:
        spreadsheet = self._client.open_by_key(self._config.spreadsheet_id)
        try:
            worksheet = spreadsheet.worksheet(self._config.worksheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=self._config.worksheet_name, rows=1000, cols=len(self._config.columns)
            )
        header = worksheet.row_values(1)
        if not header:
            worksheet.append_row(self._config.columns, value_input_option="RAW")
        return worksheet

    def existing_keys(self) -> set[tuple[str, str]]:
        """(creator_id, milestone) pairs already present in the sheet."""

        records = self._worksheet.get_all_records()
        keys = set()
        for record in records:
            creator_id = str(record.get("Creator ID", "")).strip()
            milestone = str(record.get("Milestone", "")).strip()
            if creator_id and milestone:
                keys.add((creator_id, milestone))
        return keys

    def _row_for(self, award: GiftAward, added_at: datetime) -> list:
        values_by_column = {
            "Creator ID": award.creator.creator_id,
            "Creator Name": award.creator.name,
            "Email": award.creator.email,
            "Milestone": award.milestone.label,
            "Gift Amount (USD)": award.amount_usd,
            "Joined At": award.creator.joined_at.date().isoformat(),
            "Milestone Completed At": award.completed_at.date().isoformat(),
            "Cohort Week": award.cohort_week_start.isoformat(),
            "Added At": added_at.isoformat(timespec="seconds"),
            "Status": "Pending Order",
        }
        return [values_by_column.get(col, "") for col in self._config.columns]

    def append_awards(
        self, awards: list[GiftAward], added_at: datetime | None = None
    ) -> list[GiftAward]:
        """Append rows for awards not already present in the sheet. Returns those appended."""

        if not awards:
            return []
        added_at = added_at or datetime.now(timezone.utc)
        already_present = self.existing_keys()
        to_append = [
            a for a in awards if (a.creator.creator_id, a.milestone.label) not in already_present
        ]
        if not to_append:
            return []

        rows = [self._row_for(a, added_at) for a in to_append]
        self._worksheet.append_rows(rows, value_input_option="RAW")
        return to_append
