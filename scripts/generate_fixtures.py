"""One-off generator for the demo/test fixtures under fixtures/creatoriq/.

Run with `python scripts/generate_fixtures.py` whenever you want to
regenerate the sample dataset (e.g. after changing the scenario below). The
output is deterministic (fixed random seed) so re-running without edits
produces byte-identical files.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "creatoriq"

random.seed(42)


@dataclass
class ScenarioCreator:
    creator_id: str
    name: str
    email: str
    joined_at: date
    first_post_offset: int | None  # days after joined_at, or None if never posted
    first_sale_offset: int | None  # days after joined_at, or None if never sold
    post_gift_activity: str  # "sustained" | "decayed" | "churned"


CREATORS: list[ScenarioCreator] = [
    ScenarioCreator("c-1001", "Ava Thompson", "ava.thompson@example.com", date(2026, 6, 1), 2, 9, "sustained"),
    ScenarioCreator("c-1002", "Liam Chen", "liam.chen@example.com", date(2026, 6, 1), 19, None, "churned"),
    ScenarioCreator("c-1003", "Maya Patel", "maya.patel@example.com", date(2026, 6, 8), 1, None, "decayed"),
    ScenarioCreator("c-1004", "Noah Garcia", "noah.garcia@example.com", date(2026, 6, 8), None, None, "churned"),
    ScenarioCreator("c-1005", "Sophia Kim", "sophia.kim@example.com", date(2026, 6, 15), 3, 12, "sustained"),
    ScenarioCreator("c-1006", "Ethan Rodriguez", "ethan.rodriguez@example.com", date(2026, 6, 15), 5, None, "decayed"),
    ScenarioCreator("c-1007", "Isabella Nguyen", "isabella.nguyen@example.com", date(2026, 6, 22), 2, 6, "sustained"),
    ScenarioCreator("c-1008", "Mason Lee", "mason.lee@example.com", date(2026, 6, 22), None, None, "churned"),
    ScenarioCreator("c-1009", "Olivia Martinez", "olivia.martinez@example.com", date(2026, 7, 27), 3, None, "decayed"),
    ScenarioCreator("c-1010", "Lucas Davis", "lucas.davis@example.com", date(2026, 7, 27), None, None, "churned"),
    ScenarioCreator("c-1011", "Mia Wilson", "mia.wilson@example.com", date(2026, 8, 3), None, None, "churned"),
    ScenarioCreator("c-1012", "James Brown", "james.brown@example.com", date(2026, 8, 3), 1, None, "sustained"),
]

TODAY = date(2026, 8, 5)


def build_publishers() -> dict:
    return {
        "data": [
            {
                "PublisherId": c.creator_id,
                "Name": c.name,
                "EmailAddress": c.email,
                "DateAdded": c.joined_at.isoformat(),
                "Status": "Active",
            }
            for c in CREATORS
        ]
    }


def build_activation() -> dict:
    records = []
    for c in CREATORS:
        first_post_at = (
            (c.joined_at + timedelta(days=c.first_post_offset)).isoformat()
            if c.first_post_offset is not None
            else None
        )
        first_sale_at = (
            (c.joined_at + timedelta(days=c.first_sale_offset)).isoformat()
            if c.first_sale_offset is not None
            else None
        )
        records.append(
            {
                "PublisherId": c.creator_id,
                "FirstPostDate": first_post_at,
                "FirstSaleDate": first_sale_at,
            }
        )
    return {"data": records}


def daily_activity(c: ScenarioCreator) -> list[dict]:
    """Simulate baseline pre-gift activity, a post-gift bump, then sustain/decay/churn."""

    if c.first_post_offset is None:
        gift_date = None
    else:
        gift_date = c.joined_at + timedelta(days=c.first_post_offset)

    start = c.joined_at - timedelta(days=30)
    end = min(TODAY, (gift_date or c.joined_at) + timedelta(days=60))

    records = []
    day = start
    while day <= end:
        posts = 0
        sales = 0
        gmv = 0.0

        if gift_date is None:
            # Never activated: sparse baseline activity only, never any real cadence.
            if random.random() < 0.05:
                posts = 1
        elif day < gift_date:
            if random.random() < 0.08:
                posts = 1
        else:
            days_since_gift = (day - gift_date).days
            if c.post_gift_activity == "sustained":
                if random.random() < 0.55:
                    posts = random.choice([1, 1, 2])
                if random.random() < 0.18:
                    sales = 1
                    gmv = round(random.uniform(20, 120), 2)
            elif c.post_gift_activity == "decayed":
                # Active for ~2 weeks post-gift, then tapers toward the pre-gift baseline.
                prob = max(0.08, 0.6 - 0.03 * days_since_gift)
                if random.random() < prob:
                    posts = 1
                if random.random() < prob / 4:
                    sales = 1
                    gmv = round(random.uniform(20, 80), 2)
            else:  # churned
                if days_since_gift <= 3 and random.random() < 0.4 or random.random() < 0.03:
                    posts = 1

        if c.first_sale_offset is not None:
            sale_date = c.joined_at + timedelta(days=c.first_sale_offset)
            if day == sale_date:
                sales = max(sales, 1)
                gmv = max(gmv, round(random.uniform(30, 100), 2))

        if posts or sales:
            records.append(
                {
                    "PublisherId": c.creator_id,
                    "Date": day.isoformat(),
                    "Posts": posts,
                    "Sales": sales,
                    "GMV": gmv,
                }
            )
        day += timedelta(days=1)

    return records


def build_activity() -> dict:
    records = []
    for c in CREATORS:
        records.extend(daily_activity(c))
    return {"data": records}


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURES_DIR / "publishers.json").write_text(json.dumps(build_publishers(), indent=2) + "\n")
    (FIXTURES_DIR / "activation.json").write_text(json.dumps(build_activation(), indent=2) + "\n")
    (FIXTURES_DIR / "activity.json").write_text(json.dumps(build_activity(), indent=2) + "\n")
    print(f"Wrote fixtures to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
