"""CreatorIQ (ExchangeIQ) reporting API client.

CreatorIQ is the source of truth for creator ("publisher") activation and
activity data. This module provides:

- `CreatorIQClient`: talks to the real CreatorIQ ExchangeIQ API using an
  `x-api-key` header. New-creator pulls use CreatorIQ's async "view" report
  mechanism (submit -> poll -> fetch signed result URL); first-post
  completion is derived from campaign-membership data.
- `FixtureCreatorIQClient`: reads local JSON fixtures with the same shape,
  used for local development, demos, and tests without live credentials.

Both implement the `ReportsClient` protocol consumed by the workflow layer,
so the rest of the codebase never needs to know which one is active.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Protocol

import requests

from fast_track.api.field_mapper import (
    ACTIVITY_DATE_FIELDS,
    ACTIVITY_GMV_FIELDS,
    ACTIVITY_POSTS_FIELDS,
    ACTIVITY_SALES_FIELDS,
    FIRST_POST_FIELDS,
    FIRST_SALE_FIELDS,
    PUBLISHER_EMAIL_FIELDS,
    PUBLISHER_ID_FIELDS,
    PUBLISHER_JOINED_FIELDS,
    PUBLISHER_NAME_FIELDS,
    extract_records,
    first_present,
)
from fast_track.config import CreatorIQConfig
from fast_track.models import ActivationRecord, ActivityRecord, Creator

DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "creatoriq"

logger = logging.getLogger(__name__)


class ReportsClient(Protocol):
    """Interface the workflow layer depends on -- implemented by real + fixture clients."""

    def fetch_new_creators(self, since: date, until: date) -> list[Creator]: ...

    def fetch_activation(self, creator_ids: list[str]) -> list[ActivationRecord]:
        ...

    def fetch_activity(
        self, creator_ids: list[str], start: date, end: date
    ) -> list[ActivityRecord]: ...


def _publisher_to_creator(raw: dict) -> Creator | None:
    creator_id = first_present(raw, PUBLISHER_ID_FIELDS)
    joined_at = first_present(raw, PUBLISHER_JOINED_FIELDS)
    if creator_id is None or joined_at is None:
        return None
    return Creator.from_api(
        {
            "creator_id": creator_id,
            "name": first_present(raw, PUBLISHER_NAME_FIELDS) or "",
            "email": first_present(raw, PUBLISHER_EMAIL_FIELDS) or "",
            "joined_at": joined_at,
        }
    )


def _raw_to_activation(raw: dict) -> ActivationRecord | None:
    creator_id = first_present(raw, PUBLISHER_ID_FIELDS)
    if creator_id is None:
        return None
    return ActivationRecord.from_api(
        {
            "creator_id": creator_id,
            "first_post_at": first_present(raw, FIRST_POST_FIELDS),
            "first_sale_at": first_present(raw, FIRST_SALE_FIELDS),
        }
    )


def _raw_to_activity(raw: dict) -> ActivityRecord | None:
    creator_id = first_present(raw, PUBLISHER_ID_FIELDS)
    activity_date = first_present(raw, ACTIVITY_DATE_FIELDS)
    if creator_id is None or activity_date is None:
        return None
    return ActivityRecord.from_api(
        {
            "creator_id": creator_id,
            "date": activity_date,
            "posts": first_present(raw, ACTIVITY_POSTS_FIELDS) or 0,
            "sales": first_present(raw, ACTIVITY_SALES_FIELDS) or 0,
            "gmv_usd": first_present(raw, ACTIVITY_GMV_FIELDS) or 0.0,
        }
    )


class CreatorIQClient:
    """Live client for the CreatorIQ ExchangeIQ REST API."""

    def __init__(self, config: CreatorIQConfig, session: requests.Session | None = None):
        if not config.has_credentials():
            raise ValueError(
                "CreatorIQ credentials are missing. Set CREATORIQ_BASE_URL and "
                "CREATORIQ_API_KEY, or set CREATORIQ_USE_FIXTURES=true for local testing."
            )
        self._config = config
        self._session = session or requests.Session()

    def _headers(self) -> dict:
        return {
            self._config.api_key_header: self._config.api_key,
            "Accept": "application/json",
        }

    def _submit_view_request(self, view: str, request_data: dict) -> dict:
        cfg = self._config
        url = cfg.base_url.rstrip("/") + cfg.view_path
        params: dict = {"view": view, "section": "default"}
        params.update(request_data)
        response = self._session.get(
            url, headers=self._headers(), params=params, timeout=cfg.timeout_seconds
        )
        response.raise_for_status()
        return response.json()

    def _run_view_report(self, view: str, request_data: dict) -> list[dict]:
        """Submit an async CreatorIQ "view" report and return its result rows.

        CreatorIQ's report-style list endpoints (e.g. `Reports/Publishers`)
        work like: `GET {view_path}?view=...` creates a task; poll the same
        request until `TaskStatus` is `DONE`, then fetch the rows from the
        signed URL in `Result.Headers.Location`.
        """

        cfg = self._config
        deadline = time.monotonic() + cfg.view_poll_timeout_seconds
        payload = self._submit_view_request(view, request_data)
        while payload.get("TaskStatus") != "DONE":
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"CreatorIQ view report {view!r} did not finish within "
                    f"{cfg.view_poll_timeout_seconds}s (last status: {payload.get('TaskStatus')!r})"
                )
            time.sleep(cfg.view_poll_interval_seconds)
            payload = self._submit_view_request(view, request_data)

        location = payload.get("Result", {}).get("Headers", {}).get("Location")
        if not location:
            return []
        result_response = self._session.get(location, timeout=cfg.timeout_seconds)
        result_response.raise_for_status()
        data = result_response.json()
        return data.get("results", []) if isinstance(data, dict) else data

    def fetch_new_creators(self, since: date, until: date) -> list[Creator]:
        cfg = self._config
        creators: list[Creator] = []
        skip = 0

        while True:
            rows = self._run_view_report(
                cfg.publishers_view,
                {
                    "requestData[take]": cfg.view_page_size,
                    "requestData[skip]": skip,
                    "requestData[sort][0][field]": cfg.publishers_view_sort_field,
                    "requestData[sort][0][dir]": "desc",
                },
            )
            if not rows:
                break
            logger.info("Fetched %d publisher(s) from CreatorIQ (skip=%d)", len(rows), skip)

            reached_window_start = False
            for raw in rows:
                creator = _publisher_to_creator(raw)
                if creator is None:
                    continue
                joined_date = creator.joined_at.date()
                if joined_date > until:
                    continue
                if joined_date < since:
                    reached_window_start = True
                    break
                creators.append(creator)

            if reached_window_start or len(rows) < cfg.view_page_size:
                break
            skip += cfg.view_page_size

        return creators

    def _publisher_campaign_membership(self, publisher_id: str, campaign_id: str) -> dict | None:
        cfg = self._config
        url = cfg.base_url.rstrip("/") + cfg.publisher_campaigns_path.format(publisher_id=publisher_id)
        response = self._session.get(url, headers=self._headers(), timeout=cfg.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("CampaignCollection", []):
            membership = item.get("PublisherInCampaign", {})
            if str(membership.get("CampaignId")) == str(campaign_id):
                return membership
        return None

    def fetch_activation(self, creator_ids: list[str]) -> list[ActivationRecord]:
        """First-post completion, derived from campaign-membership data.

        CreatorIQ doesn't expose a generic "activation report" endpoint;
        instead, each publisher's membership in a specific Campaign (fetched
        via `GET /publisher/{id}/campaigns`) has a `DateRequirementsCompleted`
        field once they've fulfilled that campaign's post requirements. Set
        `CREATORIQ_CAMPAIGN_ID` to the CampaignId Fast Track creators are
        added to so this can identify the right membership.

        First-sale detection isn't wired up yet: the CRM API's
        `conversionMetrics` endpoint only exposes current cumulative values
        with no per-conversion timestamp, so it can't answer "when did this
        creator's first sale happen" on its own -- see README.
        """

        if not creator_ids:
            return []
        cfg = self._config
        if not cfg.campaign_id:
            logger.warning(
                "CREATORIQ_CAMPAIGN_ID is not set, so first-post completion can't be "
                "determined yet (see README 'Adapting to your CreatorIQ account'). "
                "Returning no activation data."
            )
            return []

        records: list[ActivationRecord] = []
        for creator_id in creator_ids:
            try:
                membership = self._publisher_campaign_membership(creator_id, cfg.campaign_id)
            except requests.RequestException as exc:
                logger.warning(
                    "Failed to fetch campaign membership for publisher %s: %s", creator_id, exc
                )
                continue
            if membership is None:
                continue
            records.append(
                ActivationRecord.from_api(
                    {
                        "creator_id": creator_id,
                        "first_post_at": membership.get("DateRequirementsCompleted"),
                        "first_sale_at": None,
                    }
                )
            )
        return records

    def fetch_activity(
        self, creator_ids: list[str], start: date, end: date
    ) -> list[ActivityRecord]:
        """Daily posts/sales/GMV history for the retention dashboard.

        Not wired up yet: CreatorIQ's CRM API doesn't expose a per-day
        activity report, and per-conversion timestamps for GMV/sales aren't
        available from the endpoints confirmed so far (see
        `fetch_activation` docstring). See README for what's needed to
        finish this.
        """

        if creator_ids:
            logger.warning(
                "Daily activity sync isn't wired up yet for this CreatorIQ account -- no "
                "confirmed per-day posts/sales/GMV source. Returning no activity data."
            )
        return []


class FixtureCreatorIQClient:
    """Reads local JSON fixtures shaped like CreatorIQ API responses.

    Used for local development/demo runs and in automated tests so the rest
    of the pipeline can be exercised without live CreatorIQ credentials.
    """

    def __init__(self, fixtures_dir: Path | str = DEFAULT_FIXTURES_DIR):
        self._dir = Path(fixtures_dir)

    def _load(self, filename: str) -> dict:
        path = self._dir / filename
        if not path.exists():
            return {"data": []}
        return json.loads(path.read_text())

    def fetch_new_creators(self, since: date, until: date) -> list[Creator]:
        payload = self._load("publishers.json")
        raw_records = extract_records(payload, ["data"])
        creators = [_publisher_to_creator(raw) for raw in raw_records]
        return [
            c
            for c in creators
            if c is not None and since <= c.joined_at.date() <= until
        ]

    def fetch_activation(self, creator_ids: list[str]) -> list[ActivationRecord]:
        payload = self._load("activation.json")
        raw_records = extract_records(payload, ["data"])
        records = [_raw_to_activation(raw) for raw in raw_records]
        wanted = set(creator_ids)
        return [r for r in records if r is not None and r.creator_id in wanted]

    def fetch_activity(
        self, creator_ids: list[str], start: date, end: date
    ) -> list[ActivityRecord]:
        payload = self._load("activity.json")
        raw_records = extract_records(payload, ["data"])
        records = [_raw_to_activity(raw) for raw in raw_records]
        wanted = set(creator_ids)
        return [
            r
            for r in records
            if r is not None and r.creator_id in wanted and start <= r.activity_date <= end
        ]


def build_reports_client(config: CreatorIQConfig | None = None) -> ReportsClient:
    """Factory: returns a live CreatorIQClient, or a FixtureCreatorIQClient in dry-run/demo mode."""

    from fast_track.config import get_settings

    cfg = config or get_settings().creatoriq
    if cfg.use_fixtures or not cfg.has_credentials():
        return FixtureCreatorIQClient()
    return CreatorIQClient(cfg)
