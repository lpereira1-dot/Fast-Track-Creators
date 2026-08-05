"""Small utilities for normalizing loosely-specified CreatorIQ JSON payloads.

CreatorIQ's exact response field names can differ across accounts and API
versions (the full reference is only visible after signing into
apidocs.creatoriq.com with an account that has API access). Rather than
hard-code one schema, we look up each normalized field from a short list of
plausible source keys/paths, so a small config tweak -- not a code change --
is usually enough to adapt this to your account's real payload shape. See
README.md -> "Adapting to your CreatorIQ account".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def get_path(payload: dict, path: str) -> Any:
    """Look up a dotted path (e.g. `"Publisher.Email"`) inside a nested dict."""

    current: Any = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def first_present(payload: dict, candidates: Iterable[str]) -> Any:
    """Return the first non-null value found by trying each candidate path."""

    for candidate in candidates:
        value = get_path(payload, candidate)
        if value is not None and value != "":
            return value
    return None


def extract_records(payload: dict, response_root_candidates: Iterable[str]) -> list[dict]:
    """Pull the list of records out of a CreatorIQ response envelope.

    Tries `response_root_candidates` (typically the configured
    `CREATORIQ_RESPONSE_ROOT` first, e.g. `"data"`), then falls back to a
    handful of common envelope keys before giving up.
    """

    if isinstance(payload, list):
        return payload

    for candidate in [*response_root_candidates, "data", "items", "results", "publishers"]:
        value = get_path(payload, candidate)
        if isinstance(value, list):
            return value
    return []


# Default field candidates for each normalized attribute. Configure
# alternates via the `CREATORIQ_FIELD_MAP_*` env vars (see config.py) if your
# account's payload uses different names -- most CreatorIQ deployments use
# some subset of these PascalCase / snake_case / camelCase variants.
PUBLISHER_ID_FIELDS = ["PublisherId", "publisher_id", "Id", "id", "PublicId"]
PUBLISHER_NAME_FIELDS = ["Name", "name", "FullName", "DisplayName", "UserName"]
PUBLISHER_EMAIL_FIELDS = ["EmailAddress", "email", "Email", "PrimaryEmail"]
PUBLISHER_JOINED_FIELDS = [
    "DateAdded",
    "date_added",
    "CreatedDate",
    "created_at",
    "JoinedAt",
    "joined_at",
    "OnboardedAt",
]

FIRST_POST_FIELDS = ["FirstPostDate", "first_post_at", "FirstPostAt", "first_post_date"]
FIRST_SALE_FIELDS = [
    "FirstSaleDate",
    "first_sale_at",
    "FirstConversionDate",
    "first_conversion_at",
    "first_sale_date",
]

ACTIVITY_DATE_FIELDS = ["Date", "date", "ActivityDate", "activity_date"]
ACTIVITY_POSTS_FIELDS = ["Posts", "posts", "PostCount", "post_count"]
ACTIVITY_SALES_FIELDS = ["Sales", "sales", "Conversions", "conversions", "SalesCount"]
ACTIVITY_GMV_FIELDS = ["GMV", "gmv_usd", "Revenue", "revenue", "SalesAmount", "gmv"]
