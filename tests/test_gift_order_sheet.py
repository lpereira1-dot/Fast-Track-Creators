from datetime import date, datetime

import gspread
import pytest

from fast_track.config import GoogleSheetsConfig
from fast_track.models import Creator, GiftAward, Milestone
from fast_track.sheets.gift_order_sheet import GiftOrderSheetClient


class FakeWorksheet:
    def __init__(self):
        self._rows: list[list] = []

    def row_values(self, _row_number):
        return self._rows[0] if self._rows else []

    def append_row(self, values, value_input_option="RAW"):
        self._rows.append(list(values))

    def append_rows(self, rows, value_input_option="RAW"):
        for row in rows:
            self._rows.append(list(row))

    def get_all_records(self):
        if not self._rows:
            return []
        header = self._rows[0]
        return [dict(zip(header, row)) for row in self._rows[1:]]


class FakeSpreadsheet:
    def __init__(self):
        self._worksheets: dict[str, FakeWorksheet] = {}

    def worksheet(self, name):
        if name not in self._worksheets:
            raise gspread.WorksheetNotFound(name)
        return self._worksheets[name]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet()
        self._worksheets[title] = ws
        return ws


class FakeGspreadClient:
    def __init__(self):
        self.spreadsheets: dict[str, FakeSpreadsheet] = {}

    def open_by_key(self, spreadsheet_id):
        return self.spreadsheets.setdefault(spreadsheet_id, FakeSpreadsheet())


def make_config(tmp_path) -> GoogleSheetsConfig:
    service_account_path = tmp_path / "sa.json"
    service_account_path.write_text("{}")
    return GoogleSheetsConfig(
        spreadsheet_id="sheet-123",
        worksheet_name="Gift Card Orders",
        service_account_json_path=str(service_account_path),
        columns=["Creator ID", "Creator Name", "Email", "Milestone", "Gift Amount (USD)", "Status"],
    )


def make_award(creator_id: str, milestone: Milestone) -> GiftAward:
    creator = Creator.from_api(
        {
            "creator_id": creator_id,
            "name": f"Creator {creator_id}",
            "email": f"{creator_id}@example.com",
            "joined_at": "2026-06-01T00:00:00Z",
        }
    )
    return GiftAward(
        creator=creator,
        milestone=milestone,
        amount_usd=25.0,
        completed_at=creator.joined_at,
        cohort_week_start=date(2026, 6, 1),
    )


def test_creates_header_row_on_first_use(tmp_path):
    config = make_config(tmp_path)
    fake_client = FakeGspreadClient()
    GiftOrderSheetClient(config, gspread_client=fake_client)

    worksheet = fake_client.spreadsheets["sheet-123"].worksheet("Gift Card Orders")
    assert worksheet.row_values(1) == config.columns


def test_append_awards_writes_expected_row(tmp_path):
    config = make_config(tmp_path)
    fake_client = FakeGspreadClient()
    sheets_client = GiftOrderSheetClient(config, gspread_client=fake_client)

    award = make_award("c-1", Milestone.FIRST_POST)
    appended = sheets_client.append_awards([award], added_at=datetime(2026, 6, 2, 12, 0, 0))

    assert len(appended) == 1
    worksheet = fake_client.spreadsheets["sheet-123"].worksheet("Gift Card Orders")
    records = worksheet.get_all_records()
    assert len(records) == 1
    assert records[0]["Creator ID"] == "c-1"
    assert records[0]["Milestone"] == "First Post"
    assert records[0]["Gift Amount (USD)"] == 25.0
    assert records[0]["Status"] == "Pending Order"


def test_append_awards_is_idempotent_against_existing_sheet_rows(tmp_path):
    config = make_config(tmp_path)
    fake_client = FakeGspreadClient()
    sheets_client = GiftOrderSheetClient(config, gspread_client=fake_client)

    award = make_award("c-1", Milestone.FIRST_POST)
    sheets_client.append_awards([award])

    # A second client instance (simulating a re-run) should see the existing
    # row and refuse to add a duplicate for the same creator + milestone.
    sheets_client_2 = GiftOrderSheetClient(config, gspread_client=fake_client)
    appended_again = sheets_client_2.append_awards([award])

    assert appended_again == []
    worksheet = fake_client.spreadsheets["sheet-123"].worksheet("Gift Card Orders")
    assert len(worksheet.get_all_records()) == 1


def test_append_awards_allows_second_milestone_for_same_creator(tmp_path):
    config = make_config(tmp_path)
    fake_client = FakeGspreadClient()
    sheets_client = GiftOrderSheetClient(config, gspread_client=fake_client)

    sheets_client.append_awards([make_award("c-1", Milestone.FIRST_POST)])
    appended = sheets_client.append_awards([make_award("c-1", Milestone.FIRST_SALE)])

    assert len(appended) == 1
    worksheet = fake_client.spreadsheets["sheet-123"].worksheet("Gift Card Orders")
    assert len(worksheet.get_all_records()) == 2


def test_missing_credentials_raises():
    with pytest.raises(ValueError):
        GiftOrderSheetClient(GoogleSheetsConfig(spreadsheet_id="", service_account_json_path=""))
