"""CreatorIQ (ExchangeIQ) reporting API client.

CreatorIQ is the source of truth for creator ("publisher") activation and
activity data. This module provides:

- `CreatorIQClient`: talks to the real CreatorIQ ExchangeIQ API using an
  `x-api-key` header. Fast Track creators and their "joined" date come from
  a specific Campaign's publisher roster (`GET /campaign/{id}/publishers`);
  first-post detection is a locally-tracked proxy (see `FirstPostObserver`
  below) since CreatorIQ doesn't expose a true per-post timestamp.
- `FixtureCreatorIQClient`: reads local JSON fixtures with the same shape,
  used for local development, demos, and tests without live credentials.

Both implement the `ReportsClient` protocol consumed by the workflow layer,
so the rest of the codebase never needs to know which one is active.
"""

from __future__ import annotations

import json
import logging
import re
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

    def fetch_creator_email(self, creator_id: str) -> str:
        """Best-effort email lookup for one creator, called lazily (only for creators

        who actually qualify for a gift) rather than eagerly for everyone pulled by
        `fetch_new_creators`, since it can cost extra API calls per creator.
        """
        ...


class FirstPostObserver(Protocol):
    """Persists the locally-observed "first post" date per creator.

    CreatorIQ doesn't expose a true per-post timestamp for this account, so
    `CreatorIQClient` treats "first post" as a proxy: the date our own job
    first observes a creator's post count above zero. `StateStore` (see
    `fast_track.storage.state_store`) implements this Protocol directly.
    """

    def resolve_first_post_dates(
        self, post_counts: dict[str, int], today: date | None = None
    ) -> dict[str, date]: ...


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


_NETWORK_ID_FROM_HREF = re.compile(r"/publisher/(\d+)/campaigns")


class CreatorIQClient:
    """Live client for the CreatorIQ ExchangeIQ REST API."""

    def __init__(
        self,
        config: CreatorIQConfig,
        session: requests.Session | None = None,
        first_post_observer: FirstPostObserver | None = None,
    ):
        if not config.has_credentials():
            raise ValueError(
                "CreatorIQ credentials are missing. Set CREATORIQ_BASE_URL and "
                "CREATORIQ_API_KEY, or set CREATORIQ_USE_FIXTURES=true for local testing."
            )
        self._config = config
        self._session = session or requests.Session()
        self._first_post_observer = first_post_observer

    def _headers(self) -> dict:
        return {
            self._config.api_key_header: self._config.api_key,
            "Accept": "application/json",
        }

    def _fetch_campaign_roster(self, campaign_id: str) -> dict[str, dict]:
        """All publisher memberships in a Campaign, keyed by PublisherId.

        `GET /campaign/{id}/publishers` returns entries as a dict keyed by
        stringified indices (with a couple of stray non-publisher metadata
        keys mixed in, which are skipped). Its `page` param doesn't
        reliably paginate on the account this was confirmed against -- it
        just returns the same full roster every time -- so we walk pages
        defensively and stop as soon as one adds no new publisher ids,
        which is correct whether or not `page` actually works.
        """

        cfg = self._config
        url = cfg.base_url.rstrip("/") + cfg.campaign_publishers_path.format(campaign_id=campaign_id)
        roster: dict[str, dict] = {}
        for page in range(1, cfg.campaign_roster_max_pages + 1):
            response = self._session.get(
                url, headers=self._headers(), params={"page": page}, timeout=cfg.timeout_seconds
            )
            response.raise_for_status()
            entries = response.json().get("CampaignPublisher", {})
            if not isinstance(entries, dict) or not entries:
                break
            new_count = 0
            for entry in entries.values():
                if not isinstance(entry, dict) or "PublisherId" not in entry:
                    continue
                publisher_id = str(entry["PublisherId"])
                if publisher_id not in roster:
                    new_count += 1
                roster[publisher_id] = entry
            if new_count == 0:
                break
        return roster

    def fetch_new_creators(self, since: date, until: date) -> list[Creator]:
        """Fast Track creators, sourced from the target Campaign's roster.

        "Joined" means added to the Fast Track campaign (`DatePublisherAdded`),
        not when they joined the overall CreatorIQ network -- those can
        differ by weeks or months, and the 14-day activation window is
        meant to start from campaign add. Email is intentionally left
        blank here (fetched lazily via `fetch_creator_email` only for
        creators who actually qualify for a gift) since resolving it costs
        a few extra API calls per creator.
        """

        cfg = self._config
        if not cfg.campaign_id:
            logger.warning(
                "CREATORIQ_CAMPAIGN_ID is not set -- the campaign roster is the source "
                "of truth for who's in the Fast Track program and when they joined. "
                "Returning no creators (see README 'Adapting to your CreatorIQ account')."
            )
            return []

        try:
            roster = self._fetch_campaign_roster(cfg.campaign_id)
        except requests.RequestException as exc:
            logger.warning("Failed to fetch campaign %s roster: %s", cfg.campaign_id, exc)
            return []
        logger.info("Fetched %d publisher(s) from campaign %s roster", len(roster), cfg.campaign_id)

        creators: list[Creator] = []
        for raw in roster.values():
            creator = _publisher_to_creator(raw)
            if creator is None:
                continue
            if since <= creator.joined_at.date() <= until:
                creators.append(creator)
        return creators

    def _resolve_publisher_network_id(self, internal_id: str) -> str | None:
        """CreatorIQ uses two different publisher id schemes across endpoints.

        The campaign roster (and Reports/Publishers) key publishers by an
        internal id (e.g. `24415638`), but the single-publisher resource
        (`GET /publisher/{id}`) -- needed to reach a creator's email --
        only resolves a different "network" id (e.g. `10021983988`).
        `GET /publisher/{internal_id}/campaigns` accepts the internal id
        and its response `href` happens to contain the network id, which
        we extract here rather than guessing at a dedicated lookup endpoint.
        """

        cfg = self._config
        url = cfg.base_url.rstrip("/") + f"/crm/v1/api/publisher/{internal_id}/campaigns"
        response = self._session.get(url, headers=self._headers(), timeout=cfg.timeout_seconds)
        response.raise_for_status()
        match = _NETWORK_ID_FROM_HREF.search(response.json().get("href", ""))
        return match.group(1) if match else None

    def fetch_creator_email(self, creator_id: str) -> str:
        """Best-effort email lookup: internal id -> network id -> publisher -> contact.

        Called lazily, only for creators who actually qualify for a gift
        (see `fetch_new_creators` docstring), since this costs a few extra
        API calls. Returns "" (logging a warning) on any failure rather
        than raising, so a lookup problem for one creator doesn't block
        the rest of the run.
        """

        cfg = self._config
        try:
            network_id = self._resolve_publisher_network_id(creator_id)
            if not network_id:
                return ""
            pub_url = cfg.base_url.rstrip("/") + f"/crm/v1/api/publisher/{network_id}"
            pub_response = self._session.get(
                pub_url, headers=self._headers(), timeout=cfg.timeout_seconds
            )
            pub_response.raise_for_status()
            address_href = pub_response.json().get("Publisher", {}).get("Address", {}).get("href")
            if not address_href:
                return ""
            addr_response = self._session.get(
                address_href, headers=self._headers(), timeout=cfg.timeout_seconds
            )
            addr_response.raise_for_status()
            return addr_response.json().get("Address", {}).get("Email") or ""
        except requests.RequestException as exc:
            logger.warning("Failed to look up email for publisher %s: %s", creator_id, exc)
            return ""

    def fetch_activation(self, creator_ids: list[str]) -> list[ActivationRecord]:
        """First-post detection via a locally-observed proxy date.

        CreatorIQ's campaign roster exposes each creator's *current*
        cumulative post count (`ActualPostsTotal`) but no per-post
        timestamp, so it can't answer "when did this creator's first post
        happen" on its own. Instead, this looks up each creator's current
        post count and hands it to `first_post_observer` (a `StateStore`),
        which records -- once, the first time it's seen -- the date our own
        job observed the count go above zero, and returns that stable date
        on every later call. How closely that approximates the *true*
        first-post date depends on how often this job runs.

        First-sale detection isn't wired up yet: the CRM API's
        `conversionMetrics` endpoint only exposes current cumulative values
        with no per-conversion timestamp either -- see README.
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
        if self._first_post_observer is None:
            logger.warning(
                "No first-post observer configured -- CreatorIQ doesn't expose a true "
                "per-post timestamp, so first-post dates must be tracked locally as "
                "they're observed (see fast_track.storage.state_store.StateStore). "
                "Returning no activation data."
            )
            return []

        try:
            roster = self._fetch_campaign_roster(cfg.campaign_id)
        except requests.RequestException as exc:
            logger.warning("Failed to fetch campaign %s roster: %s", cfg.campaign_id, exc)
            return []

        post_counts = {
            str(creator_id): int((roster.get(str(creator_id)) or {}).get("ActualPostsTotal") or 0)
            for creator_id in creator_ids
        }
        observed = self._first_post_observer.resolve_first_post_dates(post_counts)
        return [
            ActivationRecord.from_api(
                {
                    "creator_id": creator_id,
                    "first_post_at": observed[str(creator_id)].isoformat(),
                    "first_sale_at": None,
                }
            )
            for creator_id in creator_ids
            if str(creator_id) in observed
        ]

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

    def fetch_creator_email(self, creator_id: str) -> str:
        payload = self._load("publishers.json")
        raw_records = extract_records(payload, ["data"])
        for raw in raw_records:
            creator = _publisher_to_creator(raw)
            if creator is not None and creator.creator_id == str(creator_id):
                return creator.email
        return ""


def build_reports_client(
    config: CreatorIQConfig | None = None,
    first_post_observer: FirstPostObserver | None = None,
) -> ReportsClient:
    """Factory: returns a live CreatorIQClient, or a FixtureCreatorIQClient in dry-run/demo mode."""

    from fast_track.config import get_settings

    cfg = config or get_settings().creatoriq
    if cfg.use_fixtures or not cfg.has_credentials():
        return FixtureCreatorIQClient()
    return CreatorIQClient(cfg, first_post_observer=first_post_observer)
