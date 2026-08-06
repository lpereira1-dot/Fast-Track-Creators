"""Unit tests for the live `CreatorIQClient` against a fake HTTP session.

These exercise the confirmed-against-a-real-account behaviors: Fast Track
creators sourced from a Campaign's publisher roster (`DatePublisherAdded`
as "joined"), lazy email resolution (roster id -> campaigns href -> network
id -> publisher -> contact), and first-post detection via a locally
persisted "first observed posting" proxy (see README "Adapting to your
CreatorIQ account").
"""

from __future__ import annotations

from datetime import date

import pytest
import requests

from fast_track.api.creatoriq import CreatorIQClient
from fast_track.config import CreatorIQConfig


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """Routes GETs to canned responses based on URL shape, and records calls."""

    def __init__(self):
        self.calls: list[tuple[str, dict, dict]] = []
        self.campaign_roster_pages: dict[str, list[list[dict]]] = {}
        self.network_id_by_internal_id: dict[str, str] = {}
        self.publishers_by_network_id: dict[str, dict] = {}
        self.contacts_by_address_id: dict[str, dict] = {}

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        if "/campaign/" in url and url.endswith("/publishers"):
            campaign_id = url.split("/campaign/")[1].split("/publishers")[0]
            page = params["page"]
            pages = self.campaign_roster_pages.get(campaign_id, [])
            entries = pages[page - 1] if page - 1 < len(pages) else []
            body = {str(i): entry for i, entry in enumerate(entries)}
            return FakeResponse({"CampaignPublisher": body})
        if "/publisher/" in url and url.endswith("/campaigns"):
            internal_id = url.split("/publisher/")[1].split("/campaigns")[0]
            network_id = self.network_id_by_internal_id.get(internal_id, "")
            return FakeResponse(
                {"href": f"https://apis.creatoriq.com/crm/v1/api/publisher/{network_id}/campaigns"}
            )
        if "/publisher/" in url and "/data/contact/" not in url:
            network_id = url.rsplit("/publisher/", 1)[1]
            return FakeResponse({"Publisher": self.publishers_by_network_id.get(network_id, {})})
        if "/data/contact/" in url:
            address_id = url.rsplit("/", 1)[-1]
            return FakeResponse({"Address": self.contacts_by_address_id.get(address_id, {})})
        raise AssertionError(f"Unexpected URL requested: {url}")


class FakeObserver:
    """Fake `FirstPostObserver`: records what it's asked to resolve."""

    def __init__(self, canned: dict[str, date] | None = None):
        self.canned = canned or {}
        self.calls: list[dict[str, int]] = []

    def resolve_first_post_dates(self, post_counts, today=None):
        self.calls.append(dict(post_counts))
        return {cid: d for cid, d in self.canned.items() if post_counts.get(cid, 0) > 0}


def _config(**overrides) -> CreatorIQConfig:
    defaults = dict(base_url="https://apis.creatoriq.com", api_key="test-key")
    defaults.update(overrides)
    return CreatorIQConfig(**defaults)


def test_fetch_new_creators_filters_by_campaign_add_date():
    session = FakeSession()
    session.campaign_roster_pages["555"] = [
        [
            {"PublisherId": 1, "PublisherName": "Alice", "DatePublisherAdded": "2026-08-01"},
            {"PublisherId": 2, "PublisherName": "Bob", "DatePublisherAdded": "2026-06-01"},
            {"PublisherId": 3, "PublisherName": "Carol", "DatePublisherAdded": "2026-07-25"},
        ]
    ]
    client = CreatorIQClient(_config(campaign_id="555"), session=session)

    creators = client.fetch_new_creators(since=date(2026, 7, 20), until=date(2026, 8, 6))

    assert {c.creator_id for c in creators} == {"1", "3"}
    alice = next(c for c in creators if c.creator_id == "1")
    assert alice.name == "Alice"
    assert alice.joined_at.date() == date(2026, 8, 1)
    assert alice.email == ""  # resolved lazily elsewhere, not here


def test_fetch_new_creators_returns_empty_without_campaign_id():
    client = CreatorIQClient(_config(campaign_id=""), session=FakeSession())

    assert client.fetch_new_creators(since=date(2026, 7, 1), until=date(2026, 8, 6)) == []


def test_fetch_creator_email_resolves_through_id_translation_chain():
    session = FakeSession()
    session.network_id_by_internal_id["24415638"] = "10021983988"
    session.publishers_by_network_id["10021983988"] = {
        "PublisherName": "alwaysdecoratingbeautifully",
        "Address": {"href": "https://apis.creatoriq.com/crm/v1/api/data/contact/129839601"},
    }
    session.contacts_by_address_id["129839601"] = {"Email": "creator@example.com"}
    client = CreatorIQClient(_config(campaign_id="555"), session=session)

    assert client.fetch_creator_email("24415638") == "creator@example.com"

    # Every hop -- including the final contact lookup -- must send the
    # x-api-key header; CreatorIQ's contact endpoint 401s without it (a
    # real bug caught by this assertion during development).
    for _url, _params, headers in session.calls:
        assert headers.get("x-api-key") == "test-key"


def test_fetch_creator_email_returns_empty_string_on_failure():
    client = CreatorIQClient(_config(campaign_id="555"), session=FakeSession())

    # No network id mapping registered -> can't resolve -> "" rather than raising.
    assert client.fetch_creator_email("unknown-id") == ""


def test_fetch_activation_uses_post_counts_and_observer():
    session = FakeSession()
    session.campaign_roster_pages["555"] = [
        [
            {"PublisherId": 10, "ActualPostsTotal": 3},
            {"PublisherId": 11, "ActualPostsTotal": 0},
        ]
    ]
    observer = FakeObserver(canned={"10": date(2026, 7, 30)})
    client = CreatorIQClient(_config(campaign_id="555"), session=session, first_post_observer=observer)

    records = client.fetch_activation(["10", "11"])

    assert len(records) == 1
    assert records[0].creator_id == "10"
    assert records[0].first_post_at.date() == date(2026, 7, 30)
    assert records[0].first_sale_at is None
    assert observer.calls == [{"10": 3, "11": 0}]


def test_fetch_activation_returns_empty_without_campaign_id():
    client = CreatorIQClient(
        _config(campaign_id=""), session=FakeSession(), first_post_observer=FakeObserver()
    )

    assert client.fetch_activation(["10"]) == []


def test_fetch_activation_returns_empty_without_observer():
    client = CreatorIQClient(_config(campaign_id="555"), session=FakeSession(), first_post_observer=None)

    assert client.fetch_activation(["10"]) == []


def test_fetch_activity_returns_empty_not_wired_up():
    client = CreatorIQClient(_config(campaign_id="555"), session=FakeSession())

    assert client.fetch_activity(["10"], date(2026, 7, 1), date(2026, 8, 1)) == []


def test_creatoriq_client_requires_credentials():
    with pytest.raises(ValueError):
        CreatorIQClient(CreatorIQConfig(base_url="", api_key=""))
