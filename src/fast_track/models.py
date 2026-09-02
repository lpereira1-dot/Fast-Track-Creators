"""Core domain models shared across the workflow, sheets, and dashboard layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class Milestone(str, Enum):
    """The two gift-triggering milestones in the activation test."""

    FIRST_POST = "first_post"
    FIRST_SALE = "first_sale"

    @property
    def label(self) -> str:
        return {"first_post": "First Post", "first_sale": "First Sale"}[self.value]


@dataclass(frozen=True)
class Creator:
    """A creator enrolled in the Fast Track program."""

    creator_id: str
    name: str
    email: str
    joined_at: datetime

    @staticmethod
    def from_api(payload: dict) -> Creator:
        return Creator(
            creator_id=str(payload["creator_id"]),
            name=payload.get("name", ""),
            email=payload.get("email", ""),
            joined_at=_parse_dt(payload["joined_at"]),
        )

    def cohort_week_start(self, week_start_weekday: int = 1) -> date:
        """The Monday (by default) of the week the creator joined."""

        joined_date = self.joined_at.date()
        # Python's Monday=0 ... Sunday=6; our config uses ISO weekday (Monday=1).
        target = week_start_weekday - 1
        delta_days = (joined_date.weekday() - target) % 7
        return joined_date - timedelta(days=delta_days)


@dataclass(frozen=True)
class ActivationRecord:
    """Activation-report data for a single creator: when milestones were hit."""

    creator_id: str
    first_post_at: datetime | None
    first_sale_at: datetime | None

    @staticmethod
    def from_api(payload: dict) -> ActivationRecord:
        return ActivationRecord(
            creator_id=str(payload["creator_id"]),
            first_post_at=_parse_dt(payload.get("first_post_at")),
            first_sale_at=_parse_dt(payload.get("first_sale_at")),
        )

    def completed_at(self, milestone: Milestone) -> datetime | None:
        return self.first_post_at if milestone is Milestone.FIRST_POST else self.first_sale_at


@dataclass(frozen=True)
class ActivityRecord:
    """A single day of activity for a creator, used for retention analysis."""

    creator_id: str
    activity_date: date
    posts: int = 0
    sales: int = 0
    gmv_usd: float = 0.0

    @staticmethod
    def from_api(payload: dict) -> ActivityRecord:
        raw_date = payload["date"]
        parsed_date = (
            raw_date if isinstance(raw_date, date) and not isinstance(raw_date, datetime)
            else datetime.fromisoformat(str(raw_date)).date()
        )
        return ActivityRecord(
            creator_id=str(payload["creator_id"]),
            activity_date=parsed_date,
            posts=int(payload.get("posts", 0)),
            sales=int(payload.get("sales", 0)),
            gmv_usd=float(payload.get("gmv_usd", 0.0)),
        )

    @property
    def is_active(self) -> bool:
        return self.posts > 0 or self.sales > 0


@dataclass(frozen=True)
class GiftAward:
    """A milestone that qualified a creator for a gift card."""

    creator: Creator
    milestone: Milestone
    amount_usd: float
    completed_at: datetime
    cohort_week_start: date
    # When the award was written to the gift-order sheet (weekly job).
    # Retention pre/post windows anchor on this date, not `completed_at`,
    # because creators typically post days before the card is ordered.
    added_at: datetime | None = None


@dataclass(frozen=True)
class CreatorEmailLog:
    """A record of one lifecycle email type sent to one creator (see

    `workflow/creator_emails.py` and `StateStore.all_creator_emails`).
    """

    creator_id: str
    creator_name: str
    creator_email: str
    email_type: str
    last_sent_at: date
    send_count: int
