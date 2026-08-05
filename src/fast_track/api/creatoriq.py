"""CreatorIQ (ExchangeIQ) reporting API client.

CreatorIQ is the source of truth for creator ("publisher") activation and
activity data. This module provides:

- `CreatorIQClient`: talks to the real CreatorIQ REST API using an
  `x-api-key` header, with generic cursor-based pagination.
- `FixtureCreatorIQClient`: reads local JSON fixtures with the same shape,
  used for local development, demos, and tests without live credentials.

Both implement the `ReportsClient` protocol consumed by the workflow layer,
so the rest of the codebase never needs to know which one is active.
"""

from __future__ import annotations

import json
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

    def _paginated_get(self, path: str, params: dict) -> list[dict]:
        cfg = self._config
        url = cfg.base_url.rstrip("/") + path
        records: list[dict] = []
        query = dict(params)
        query.setdefault("limit", cfg.page_size)
        cursor: str | None = None

        while True:
            if cursor:
                query[cfg.cursor_param] = cursor
            response = self._session.get(
                url, headers=self._headers(), params=query, timeout=cfg.timeout_seconds
            )
            response.raise_for_status()
            payload = response.json()
            page_records = extract_records(payload, [cfg.response_root])
            records.extend(page_records)

            cursor = payload.get(cfg.next_cursor_key) if isinstance(payload, dict) else None
            if not cursor or not page_records:
                break

        return records

    def fetch_new_creators(self, since: date, until: date) -> list[Creator]:
        cfg = self._config
        params = {
            "date_added_after": since.isoformat(),
            "date_added_before": until.isoformat(),
        }
        if cfg.campaign_id:
            params["campaign_id"] = cfg.campaign_id
        if cfg.publisher_list_id:
            params["list_id"] = cfg.publisher_list_id

        raw_records = self._paginated_get(cfg.publishers_path, params)
        creators = [_publisher_to_creator(raw) for raw in raw_records]
        return [c for c in creators if c is not None]

    def fetch_activation(self, creator_ids: list[str]) -> list[ActivationRecord]:
        if not creator_ids:
            return []
        params = {"publisher_ids": ",".join(creator_ids)}
        raw_records = self._paginated_get(self._config.activation_report_path, params)
        records = [_raw_to_activation(raw) for raw in raw_records]
        return [r for r in records if r is not None]

    def fetch_activity(
        self, creator_ids: list[str], start: date, end: date
    ) -> list[ActivityRecord]:
        if not creator_ids:
            return []
        params = {
            "publisher_ids": ",".join(creator_ids),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        raw_records = self._paginated_get(self._config.activity_report_path, params)
        records = [_raw_to_activity(raw) for raw in raw_records]
        return [r for r in records if r is not None]


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
