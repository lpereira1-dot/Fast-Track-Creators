"""Central configuration for the Fast Track creator gift-card program.

All values are read from environment variables (typically populated via a
`.env` file locally, or via GitHub Actions / Cursor Cloud secrets in
scheduled runs) so no credentials ever need to be hard-coded or committed.

Every field below is built with `field(default_factory=...)` rather than a
plain default so that `os.environ` is re-read each time a config object is
constructed (plain dataclass defaults are only evaluated once, at class
definition time) -- this matters both for tests that monkeypatch env vars
and for long-lived processes (e.g. the dashboard) that should pick up
freshly-injected secrets on the next run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str) -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class ProgramRules:
    """The business rules of the gift-card activation test."""

    activation_window_days: int = field(
        default_factory=lambda: _env_int("ACTIVATION_WINDOW_DAYS", 14)
    )
    first_post_gift_usd: float = field(
        default_factory=lambda: _env_float("FIRST_POST_GIFT_USD", 25.0)
    )
    first_sale_gift_usd: float = field(
        default_factory=lambda: _env_float("FIRST_SALE_GIFT_USD", 25.0)
    )
    retention_window_days: int = field(
        default_factory=lambda: _env_int("RETENTION_WINDOW_DAYS", 30)
    )
    # ISO weekday (1=Monday) used to bucket creators into weekly cohorts.
    cohort_week_start_weekday: int = field(
        default_factory=lambda: _env_int("COHORT_WEEK_START_WEEKDAY", 1)
    )


@dataclass(frozen=True)
class CreatorIQConfig:
    """Connection settings for the CreatorIQ (ExchangeIQ) reporting API.

    CreatorIQ calls creators "publishers". Auth is a static `x-api-key`
    header (request one from your CreatorIQ account rep / support@creatoriq.com).

    Confirmed against a live account: CreatorIQ's real API is namespaced
    under `/crm/v1/api/...` (NOT a generic `/v1/...` REST tree), and its
    "list" endpoints are asynchronous report "views" -- you submit a
    `GET /crm/v1/api/view?view=<ViewName>&requestData[...]=...` request,
    poll the returned `TaskId` until `TaskStatus` is `DONE`, then fetch the
    actual rows from the signed S3 URL in `Result.Headers.Location`. See
    README.md "Adapting to your CreatorIQ account" for how to tune this.
    """

    base_url: str = field(
        default_factory=lambda: _env_str("CREATORIQ_BASE_URL", "https://apis.creatoriq.com")
    )
    api_key: str = field(default_factory=lambda: _env_str("CREATORIQ_API_KEY", ""))
    api_key_header: str = field(
        default_factory=lambda: _env_str("CREATORIQ_API_KEY_HEADER", "x-api-key")
    )

    # Async "view" report endpoint used for the new-creators pull, e.g.
    # GET {base_url}{view_path}?view={publishers_view}&requestData[take]=...
    view_path: str = field(default_factory=lambda: _env_str("CREATORIQ_VIEW_PATH", "/crm/v1/api/view"))
    publishers_view: str = field(
        default_factory=lambda: _env_str("CREATORIQ_PUBLISHERS_VIEW", "Reports/Publishers")
    )
    # Field the publishers view is sorted by (descending) so we can walk
    # newest-first and stop as soon as we cross the lookback window --
    # pulling all publishers unsorted isn't practical on large accounts.
    publishers_view_sort_field: str = field(
        default_factory=lambda: _env_str("CREATORIQ_PUBLISHERS_VIEW_SORT_FIELD", "RecruitingStarted")
    )
    view_page_size: int = field(default_factory=lambda: _env_int("CREATORIQ_VIEW_PAGE_SIZE", 200))
    view_poll_interval_seconds: float = field(
        default_factory=lambda: _env_float("CREATORIQ_VIEW_POLL_INTERVAL_SECONDS", 2.0)
    )
    view_poll_timeout_seconds: float = field(
        default_factory=lambda: _env_float("CREATORIQ_VIEW_POLL_TIMEOUT_SECONDS", 60.0)
    )

    # Path (relative to base_url) for a single publisher's campaign
    # memberships, used to derive "first post" completion from that
    # membership's `DateRequirementsCompleted` field. `{publisher_id}` is
    # substituted in.
    publisher_campaigns_path: str = field(
        default_factory=lambda: _env_str(
            "CREATORIQ_PUBLISHER_CAMPAIGNS_PATH", "/crm/v1/api/publisher/{publisher_id}/campaigns"
        )
    )

    # The CreatorIQ CampaignId that Fast Track creators are added to and
    # must complete post requirements for -- required to know which
    # campaign membership's `DateRequirementsCompleted` represents the
    # Fast Track "first post" milestone (a creator may belong to many
    # unrelated campaigns). Leave blank until confirmed; first-post
    # detection is skipped (with a warning) while unset.
    campaign_id: str = field(default_factory=lambda: _env_str("CREATORIQ_CAMPAIGN_ID", ""))
    publisher_list_id: str = field(
        default_factory=lambda: _env_str("CREATORIQ_PUBLISHER_LIST_ID", "")
    )

    timeout_seconds: int = field(
        default_factory=lambda: _env_int("CREATORIQ_TIMEOUT_SECONDS", 30)
    )

    # When true (or when no api_key/base_url configured), local JSON fixtures
    # are used instead of live HTTP calls. Handy for demos, tests, and dry runs.
    use_fixtures: bool = field(
        default_factory=lambda: _env_bool("CREATORIQ_USE_FIXTURES", False)
    )

    def has_credentials(self) -> bool:
        return bool(self.api_key and self.base_url)


@dataclass(frozen=True)
class GoogleSheetsConfig:
    """Connection settings for the gift-card ordering Google Sheet."""

    spreadsheet_id: str = field(default_factory=lambda: _env_str("GIFT_ORDER_SHEET_ID", ""))
    worksheet_name: str = field(
        default_factory=lambda: _env_str("GIFT_ORDER_WORKSHEET_NAME", "Gift Card Orders")
    )
    service_account_json_path: str = field(
        default_factory=lambda: _env_str("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    )
    # Column headers expected/created on the ordering worksheet, in order.
    columns: list[str] = field(
        default_factory=lambda: _env_list(
            "GIFT_ORDER_SHEET_COLUMNS",
            "Creator ID,Creator Name,Email,Milestone,Gift Amount (USD),"
            "Joined At,Milestone Completed At,Cohort Week,Added At,Status",
        )
    )

    def has_credentials(self) -> bool:
        return bool(self.spreadsheet_id and self.service_account_json_path)


@dataclass(frozen=True)
class StorageConfig:
    """Local persistence used to track state between workflow runs."""

    db_path: str = field(default_factory=lambda: _env_str("FAST_TRACK_DB_PATH", "data/fast_track.db"))


@dataclass(frozen=True)
class Settings:
    program: ProgramRules = field(default_factory=ProgramRules)
    creatoriq: CreatorIQConfig = field(default_factory=CreatorIQConfig)
    sheets: GoogleSheetsConfig = field(default_factory=GoogleSheetsConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def get_settings() -> Settings:
    """Build a fresh Settings object from the current environment.

    Using a function (rather than a module-level singleton) keeps tests free
    to monkeypatch environment variables between cases.
    """

    return Settings()
