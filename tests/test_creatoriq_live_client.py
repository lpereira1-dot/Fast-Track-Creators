"""Unit tests for the live `CreatorIQClient` against a fake HTTP session.

These exercise the confirmed-against-a-real-account behaviors: Fast Track
creators sourced from a Campaign's publisher roster (`DatePublisherAdded`
as "joined"), lazy email resolution (roster id -> campaigns href -> network
id -> publisher -> contact), first-post detection via a locally persisted
"first observed posting" proxy, and first-sale detection via real
per-transaction dates from the e-commerce transactions endpoint (see
README "Adapting to your CreatorIQ account").
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
        self.transactions: list[dict] = []
        self.transactions_page_size = 100

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        if url.endswith("/ecommerce/transactions"):
            page = params["Page"]
            page_size = params["PageSize"]
            start = (page - 1) * page_size
            rows = self.transactions[start : start + page_size]
            return FakeResponse(
                {"count": len(self.transactions), "page": page, "size": page_size, "data": rows}
            )
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


def test_fetch_activation_skips_first_post_but_still_checks_sales_without_observer():
    session = FakeSession()
    session.transactions = [
        {"PublisherId": 10, "TransactionDate": "2026-07-30 10:00:00", "Status": "pending"},
    ]
    client = CreatorIQClient(_config(campaign_id="555"), session=session, first_post_observer=None)

    records = client.fetch_activation(["10"])

    assert len(records) == 1
    assert records[0].first_post_at is None
    assert records[0].first_sale_at.date() == date(2026, 7, 30)


def test_fetch_activation_includes_first_sale_and_counts_pending_transactions():
    session = FakeSession()
    session.transactions = [
        {"PublisherId": 10, "TransactionDate": "2026-08-01 09:00:00", "Status": "Approved"},
        # Earlier, still-pending transaction should win as the *first* sale.
        {"PublisherId": 10, "TransactionDate": "2026-07-20 22:12:30", "Status": "pending"},
        {"PublisherId": 11, "TransactionDate": "2026-07-25 12:00:00", "Status": "pending"},
    ]
    client = CreatorIQClient(_config(campaign_id="555"), session=session, first_post_observer=None)

    records = {r.creator_id: r for r in client.fetch_activation(["10", "11", "12"])}

    assert records["10"].first_sale_at.date() == date(2026, 7, 20)
    assert records["11"].first_sale_at.date() == date(2026, 7, 25)
    assert "12" not in records


def test_fetch_activation_excludes_declined_and_reversed_transactions():
    session = FakeSession()
    session.transactions = [
        {"PublisherId": 10, "TransactionDate": "2026-07-20 00:00:00", "Status": "declined"},
        {"PublisherId": 10, "TransactionDate": "2026-07-22 00:00:00", "DeclineReason": "fraud"},
        {"PublisherId": 10, "TransactionDate": "2026-07-25 00:00:00", "Status": "pending"},
    ]
    client = CreatorIQClient(_config(campaign_id="555"), session=session, first_post_observer=None)

    records = client.fetch_activation(["10"])

    assert len(records) == 1
    assert records[0].first_sale_at.date() == date(2026, 7, 25)


def test_fetch_activation_paginates_transactions():
    session = FakeSession()
    session.transactions = [
        {"PublisherId": i, "TransactionDate": "2026-07-20 00:00:00", "Status": "pending"}
        for i in range(250)
    ]
    client = CreatorIQClient(_config(campaign_id="555"), session=session, first_post_observer=None)

    records = client.fetch_activation([str(i) for i in range(250)])

    assert len(records) == 250
    transaction_calls = [c for c in session.calls if c[0].endswith("/ecommerce/transactions")]
    assert len(transaction_calls) == 3  # default page size 100 -> 3 pages for 250 rows


def test_fetch_activity_returns_daily_sales_and_gmv_from_transactions():
    session = FakeSession()
    session.transactions = [
        {
            "PublisherId": 10,
            "TransactionDate": "2026-07-20 10:00:00",
            "Status": "pending",
            "SaleAmount": 100.0,
        },
        {
            "PublisherId": 10,
            "TransactionDate": "2026-07-20 18:00:00",
            "Status": "Approved",
            "SaleAmount": 50.0,
        },
        {
            "PublisherId": 10,
            "TransactionDate": "2026-06-01 00:00:00",  # outside requested window
            "Status": "pending",
            "SaleAmount": 999.0,
        },
        {
            "PublisherId": 99,  # not a requested creator
            "TransactionDate": "2026-07-20 10:00:00",
            "Status": "pending",
            "SaleAmount": 10.0,
        },
        {
            "PublisherId": 10,
            "TransactionDate": "2026-07-21 00:00:00",
            "Status": "declined",
            "SaleAmount": 500.0,
        },
    ]
    client = CreatorIQClient(_config(campaign_id="555"), session=session)

    records = client.fetch_activity(["10"], date(2026, 7, 1), date(2026, 7, 31))

    assert len(records) == 1
    assert records[0].creator_id == "10"
    assert records[0].activity_date == date(2026, 7, 20)
    assert records[0].posts == 0
    assert records[0].sales == 2
    assert records[0].gmv_usd == 150.0


def test_fetch_activity_returns_empty_without_campaign_id():
    client = CreatorIQClient(_config(campaign_id=""), session=FakeSession())

    assert client.fetch_activity(["10"], date(2026, 7, 1), date(2026, 8, 1)) == []


def test_creatoriq_client_requires_credentials():
    with pytest.raises(ValueError):
        CreatorIQClient(CreatorIQConfig(base_url="", api_key=""))
