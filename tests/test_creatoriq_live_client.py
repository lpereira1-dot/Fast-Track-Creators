"""Unit tests for the live `CreatorIQClient` against a fake HTTP session.

These exercise the async "view" report flow (submit -> poll -> fetch result)
and the campaign-membership-based first-post lookup, using response shapes
confirmed against a real CreatorIQ account (see README "Adapting to your
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
        self.calls: list[tuple[str, dict]] = []
        self.view_results_by_skip: dict[int, dict] = {}
        self.campaign_collections_by_publisher: dict[str, dict] = {}

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if url.endswith("/crm/v1/api/view"):
            skip = params["requestData[skip]"]
            return FakeResponse(
                {
                    "TaskId": "task-1",
                    "TaskStatus": "DONE",
                    "Result": {"Headers": {"Location": f"https://fake-results/{skip}.json"}},
                }
            )
        if url.startswith("https://fake-results/"):
            skip = int(url.rsplit("/", 1)[-1].split(".")[0])
            return FakeResponse(self.view_results_by_skip.get(skip, {"results": []}))
        if "/publisher/" in url and url.endswith("/campaigns"):
            publisher_id = url.split("/publisher/")[1].split("/campaigns")[0]
            return FakeResponse(
                self.campaign_collections_by_publisher.get(
                    publisher_id, {"CampaignCollection": []}
                )
            )
        raise AssertionError(f"Unexpected URL requested: {url}")


def _config(**overrides) -> CreatorIQConfig:
    defaults = dict(
        base_url="https://apis.creatoriq.com",
        api_key="test-key",
        view_page_size=2,
    )
    defaults.update(overrides)
    return CreatorIQConfig(**defaults)


def test_fetch_new_creators_stops_at_window_start_across_pages():
    session = FakeSession()
    session.view_results_by_skip[0] = {
        "results": [
            {
                "PublisherId": 3,
                "PublisherName": "Carol",
                "Email": "carol@example.com",
                "RecruitingStarted": "2026-08-05",
            },
            {
                "PublisherId": 2,
                "PublisherName": "Bob",
                "Email": "bob@example.com",
                "RecruitingStarted": "2026-07-25",
            },
        ]
    }
    session.view_results_by_skip[2] = {
        "results": [
            {
                "PublisherId": 1,
                "PublisherName": "Alice",
                "Email": "alice@example.com",
                "RecruitingStarted": "2026-07-15",  # older than `since` -> should stop here
            },
            {
                "PublisherId": 0,
                "PublisherName": "Zed",
                "Email": "zed@example.com",
                "RecruitingStarted": "2026-07-01",
            },
        ]
    }
    client = CreatorIQClient(_config(), session=session)

    creators = client.fetch_new_creators(since=date(2026, 7, 20), until=date(2026, 8, 6))

    assert [c.creator_id for c in creators] == ["3", "2"]
    assert creators[0].email == "carol@example.com"
    assert creators[0].name == "Carol"
    assert creators[0].joined_at.date() == date(2026, 8, 5)
    # Only paged once past the first page (stopped before a third page).
    view_calls = [c for c in session.calls if c[0].endswith("/crm/v1/api/view")]
    assert len(view_calls) == 2


def test_fetch_new_creators_stops_when_page_is_short():
    session = FakeSession()
    session.view_results_by_skip[0] = {
        "results": [
            {
                "PublisherId": 1,
                "PublisherName": "Alice",
                "Email": "alice@example.com",
                "RecruitingStarted": "2026-08-01",
            }
        ]
    }
    client = CreatorIQClient(_config(), session=session)

    creators = client.fetch_new_creators(since=date(2026, 7, 1), until=date(2026, 8, 6))

    assert [c.creator_id for c in creators] == ["1"]
    view_calls = [c for c in session.calls if c[0].endswith("/crm/v1/api/view")]
    assert len(view_calls) == 1


def test_fetch_activation_uses_matching_campaign_membership():
    session = FakeSession()
    session.campaign_collections_by_publisher["10"] = {
        "CampaignCollection": [
            {
                "PublisherInCampaign": {
                    "CampaignId": 999,
                    "DateRequirementsCompleted": "2026-01-01",
                }
            },
            {
                "PublisherInCampaign": {
                    "CampaignId": 555,
                    "DateRequirementsCompleted": "2026-07-30",
                }
            },
        ]
    }
    client = CreatorIQClient(_config(campaign_id="555"), session=session)

    records = client.fetch_activation(["10"])

    assert len(records) == 1
    assert records[0].creator_id == "10"
    assert records[0].first_post_at.date() == date(2026, 7, 30)
    assert records[0].first_sale_at is None


def test_fetch_activation_skips_publisher_not_in_target_campaign():
    session = FakeSession()
    session.campaign_collections_by_publisher["10"] = {
        "CampaignCollection": [
            {"PublisherInCampaign": {"CampaignId": 999, "DateRequirementsCompleted": "2026-01-01"}}
        ]
    }
    client = CreatorIQClient(_config(campaign_id="555"), session=session)

    assert client.fetch_activation(["10"]) == []


def test_fetch_activation_returns_empty_without_campaign_id():
    client = CreatorIQClient(_config(campaign_id=""), session=FakeSession())

    assert client.fetch_activation(["10"]) == []


def test_fetch_activity_returns_empty_not_wired_up():
    client = CreatorIQClient(_config(), session=FakeSession())

    assert client.fetch_activity(["10"], date(2026, 7, 1), date(2026, 8, 1)) == []


def test_creatoriq_client_requires_credentials():
    with pytest.raises(ValueError):
        CreatorIQClient(CreatorIQConfig(base_url="", api_key=""))
