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
    under `/crm/v1/api/...` (NOT a generic `/v1/...` REST tree). Fast Track
    creators and their "joined" date come from a specific Campaign's
    publisher roster (`GET /crm/v1/api/campaign/{id}/publishers`) rather
    than a generic "new creators" endpoint -- see README.md "Adapting to
    your CreatorIQ account" for the full picture.
    """

    base_url: str = field(
        default_factory=lambda: _env_str("CREATORIQ_BASE_URL", "https://apis.creatoriq.com")
    )
    api_key: str = field(default_factory=lambda: _env_str("CREATORIQ_API_KEY", ""))
    api_key_header: str = field(
        default_factory=lambda: _env_str("CREATORIQ_API_KEY_HEADER", "x-api-key")
    )

    # Path (relative to base_url) for a Campaign's full publisher roster --
    # the source of truth for who's in the Fast Track program, when they
    # joined (`DatePublisherAdded`), and their current post count
    # (`ActualPostsTotal`, used as a first-post proxy -- see
    # `CreatorIQClient.fetch_activation`). `{campaign_id}` is substituted
    # in. Confirmed to return the *entire* roster in one call for a
    # program-sized campaign (its `page` param doesn't reliably paginate),
    # so this is fetched once per run and looked up in-memory rather than
    # calling per-publisher (which is both slow and easy to rate-limit at
    # ~1000s of creators).
    campaign_publishers_path: str = field(
        default_factory=lambda: _env_str(
            "CREATORIQ_CAMPAIGN_PUBLISHERS_PATH", "/crm/v1/api/campaign/{campaign_id}/publishers"
        )
    )
    # Safety cap on how many roster pages to walk (stops automatically once
    # a page adds no new publisher ids -- this just bounds the worst case
    # if an account's `page` param actually works and the roster is huge).
    campaign_roster_max_pages: int = field(
        default_factory=lambda: _env_int("CREATORIQ_CAMPAIGN_ROSTER_MAX_PAGES", 50)
    )

    # E-commerce transactions endpoint -- the source of truth for "first
    # sale", with a real per-transaction `TransactionDate` (unlike
    # `conversionMetrics`, which only exposes current cumulative values).
    # Transactions are attributed via CJ Affiliate (Commission Junction);
    # `pending` (not yet paid out) transactions count as qualifying sales
    # for Fast Track, same as `Approved`/`Confirmed` ones.
    transactions_path: str = field(
        default_factory=lambda: _env_str(
            "CREATORIQ_TRANSACTIONS_PATH", "/crm/v1/api/ecommerce/transactions"
        )
    )
    transactions_page_size: int = field(
        default_factory=lambda: _env_int("CREATORIQ_TRANSACTIONS_PAGE_SIZE", 100)
    )

    # The CreatorIQ CampaignId that Fast Track creators are added to --
    # required to pull "new" creators (via that campaign's roster,
    # `DatePublisherAdded`), check first-post status (`ActualPostsTotal`),
    # and scope the transactions lookup for first-sale detection (see
    # `CreatorIQClient.fetch_activation`). Without this set, all of the
    # above are skipped with a warning rather than guessing at an
    # unrelated campaign.
    campaign_id: str = field(default_factory=lambda: _env_str("CREATORIQ_CAMPAIGN_ID", ""))

    # Bulk-communication endpoint (send emails to publishers). Doesn't
    # require a `FromMcn` value, unlike the campaign-scoped
    # `CampaignMessaging` endpoint -- see `CreatorEmailConfig` below and
    # `CreatorIQClient.send_bulk_email`.
    sendbulk_path: str = field(
        default_factory=lambda: _env_str(
            "CREATORIQ_SENDBULK_PATH", "/crm/v1/api/communication/sendBulk"
        )
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
class CreatorEmailConfig:
    """Settings for the creator lifecycle email reminders.

    Sent via CreatorIQ's own bulk-communication endpoint (`POST
    /crm/v1/api/communication/sendBulk`) rather than a separate email
    service -- it accepts an HTML `MessageContent`, a subject, and a list
    of publisher ids, and doesn't require a `FromMcn` value (unlike the
    campaign-scoped `CampaignMessaging` endpoint, which does but has no
    discoverable way to look that value up). See
    `src/fast_track/emails/templates.py` for the four email bodies and
    `src/fast_track/workflow/creator_emails.py` for send-trigger logic.
    """

    # How often an unresolved reminder (post or sale) repeats.
    reminder_interval_days: int = field(
        default_factory=lambda: _env_int("CREATOR_EMAIL_REMINDER_INTERVAL_DAYS", 2)
    )
    # Day (since joining) the "still hasn't posted" reminder starts.
    post_reminder_start_day: int = field(
        default_factory=lambda: _env_int("CREATOR_EMAIL_POST_REMINDER_START_DAY", 7)
    )
    creator_portal_url: str = field(
        default_factory=lambda: _env_str(
            "CREATOR_PORTAL_URL", "https://influencers.wayfair.com/connect/#welcome"
        )
    )
    getting_started_guide_url: str = field(
        default_factory=lambda: _env_str(
            "GETTING_STARTED_GUIDE_URL", "https://canva.link/larewvo17ofsi70"
        )
    )
    posting_guide_url: str = field(
        default_factory=lambda: _env_str("POSTING_GUIDE_URL", "https://canva.link/t0bbxzuvsgek58m")
    )
    creator_collective_url: str = field(
        default_factory=lambda: _env_str(
            "CREATOR_COLLECTIVE_URL", "https://influencers.wayfair.com/connect/#ProgramTiers"
        )
    )
    # Safety gate: even with real CreatorIQ credentials configured, sends
    # are forced into dry-run mode until this is explicitly set true --
    # separate from CREATORIQ_USE_FIXTURES, so this can be turned on/off
    # independently of demo mode once you're ready for a real first send.
    sending_enabled: bool = field(
        default_factory=lambda: _env_bool("CREATOR_EMAIL_SENDING_ENABLED", False)
    )


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
    creator_email: CreatorEmailConfig = field(default_factory=CreatorEmailConfig)


def get_settings() -> Settings:
    """Build a fresh Settings object from the current environment.

    Using a function (rather than a module-level singleton) keeps tests free
    to monkeypatch environment variables between cases.
    """

    return Settings()
