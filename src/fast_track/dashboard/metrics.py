"""Pre/post gift-card retention metrics.

For every creator who earned at least one gift card, we look at their daily
activity for `window_days` (default 30) before and after their "gift date"
(the date their first gift row was added to the ordering sheet) and compare
activity levels and retention.

Kept as plain pandas transformations (no Streamlit imports) so the math is
independently unit-testable -- `dashboard/app.py` is a thin rendering layer
on top of these functions.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

from fast_track.models import ActivityRecord, GiftAward, Milestone


def _gift_anchor_date(award: GiftAward) -> date:
    """First day of the post-gift window for this award.

  Uses the day *after* the gift row was added to the ordering sheet so a
  creator's qualifying first post (often the same calendar day as the sheet
  sync) lands in the pre-gift period.
    """

    sheet_day = award.added_at.date() if award.added_at is not None else award.completed_at.date()
    return sheet_day + timedelta(days=1)


def build_gift_events(awards: list[GiftAward]) -> pd.DataFrame:
    """One row per creator: earliest gift-sheet date + which gifts they earned."""

    grouped: dict[str, list[GiftAward]] = defaultdict(list)
    for award in awards:
        grouped[award.creator.creator_id].append(award)

    rows = []
    for creator_id, creator_awards in grouped.items():
        creator = creator_awards[0].creator
        gift_date = min(_gift_anchor_date(a) for a in creator_awards)
        rows.append(
            {
                "creator_id": creator_id,
                "name": creator.name,
                "email": creator.email,
                "joined_at": creator.joined_at.date(),
                "gift_date": gift_date,
                "milestones": ", ".join(sorted(a.milestone.label for a in creator_awards)),
                "total_gift_usd": sum(a.amount_usd for a in creator_awards),
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "creator_id",
                "name",
                "email",
                "joined_at",
                "gift_date",
                "milestones",
                "total_gift_usd",
            ]
        )
    return pd.DataFrame(rows).sort_values("gift_date").reset_index(drop=True)


def merge_activity_records(records: list[ActivityRecord]) -> list[ActivityRecord]:
    """Combine activity rows for the same creator/day, summing posts/sales/gmv."""

    merged: dict[tuple[str, date], ActivityRecord] = {}
    for record in records:
        key = (record.creator_id, record.activity_date)
        existing = merged.get(key)
        if existing is None:
            merged[key] = record
            continue
        merged[key] = ActivityRecord(
            creator_id=record.creator_id,
            activity_date=record.activity_date,
            posts=existing.posts + record.posts,
            sales=existing.sales + record.sales,
            gmv_usd=existing.gmv_usd + record.gmv_usd,
        )
    return list(merged.values())


def milestone_activity_records(
    awards: list[GiftAward],
    first_post_by_creator: dict[str, date] | None = None,
) -> list[ActivityRecord]:
    """One-day markers from gift milestones and locally-observed first posts.

    CreatorIQ does not expose per-day post history, so the retention
    dashboard treats the first-post observation date (and first-post gift
    milestone date) as a single posting day. First-sale milestones provide
    a sales fallback when transaction sync is empty.
    """

    first_post_by_creator = first_post_by_creator or {}
    records: list[ActivityRecord] = []
    awarded_first_post: set[str] = set()

    for award in awards:
        creator_id = award.creator.creator_id
        day = award.completed_at.date()
        if award.milestone is Milestone.FIRST_POST:
            awarded_first_post.add(creator_id)
            records.append(
                ActivityRecord(
                    creator_id=creator_id,
                    activity_date=day,
                    posts=1,
                    sales=0,
                    gmv_usd=0.0,
                )
            )
        elif award.milestone is Milestone.FIRST_SALE:
            records.append(
                ActivityRecord(
                    creator_id=creator_id,
                    activity_date=day,
                    posts=0,
                    sales=1,
                    gmv_usd=0.0,
                )
            )

    for creator_id, day in first_post_by_creator.items():
        if creator_id in awarded_first_post:
            continue
        records.append(
            ActivityRecord(
                creator_id=creator_id,
                activity_date=day,
                posts=1,
                sales=0,
                gmv_usd=0.0,
            )
        )

    return records


def build_daily_offsets(
    gift_events: pd.DataFrame,
    activity_records: list[ActivityRecord],
    window_days: int,
    as_of: date | None = None,
) -> pd.DataFrame:
    """Long-format table: one row per (creator, day offset from -window..+window).

    `offset` is negative for days before the gift, positive for days after,
    0 for the gift day itself. Days with no matching activity record are
    filled with zero posts/sales/gmv (i.e. "no activity that day", not
    "missing data") -- days beyond `as_of` (default: today) are dropped
    since they haven't happened yet.
    """

    as_of = as_of or date.today()
    activity_by_creator: dict[str, dict[date, ActivityRecord]] = defaultdict(dict)
    for record in activity_records:
        activity_by_creator[record.creator_id][record.activity_date] = record

    rows = []
    for _, event in gift_events.iterrows():
        creator_id = event["creator_id"]
        gift_date = event["gift_date"]
        acts = activity_by_creator.get(creator_id, {})
        for offset in range(-window_days, window_days + 1):
            day = gift_date + timedelta(days=offset)
            if day > as_of:
                continue
            record = acts.get(day)
            posts = record.posts if record else 0
            sales = record.sales if record else 0
            gmv = record.gmv_usd if record else 0.0
            rows.append(
                {
                    "creator_id": creator_id,
                    "name": event["name"],
                    "offset": offset,
                    "period": "pre" if offset < 0 else ("gift_day" if offset == 0 else "post"),
                    "date": day,
                    "posts": posts,
                    "sales": sales,
                    "gmv_usd": gmv,
                    "active": bool(posts > 0 or sales > 0),
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "creator_id",
            "name",
            "offset",
            "period",
            "date",
            "posts",
            "sales",
            "gmv_usd",
            "active",
        ],
    )


def retention_curve(daily_offsets: pd.DataFrame) -> pd.DataFrame:
    """Aggregate by day-offset: % of creators active, and average posts/sales, per offset."""

    if daily_offsets.empty:
        return pd.DataFrame(columns=["offset", "pct_active", "avg_posts", "avg_sales", "n_creators"])
    grouped = daily_offsets.groupby("offset").agg(
        pct_active=("active", "mean"),
        avg_posts=("posts", "mean"),
        avg_sales=("sales", "mean"),
        n_creators=("creator_id", "nunique"),
    )
    grouped["pct_active"] = grouped["pct_active"] * 100
    return grouped.reset_index().sort_values("offset")


def summarize_retention(daily_offsets: pd.DataFrame, window_days: int) -> dict:
    """Headline pre/post retention numbers for the dashboard's summary cards."""

    if daily_offsets.empty:
        return {
            "n_creators": 0,
            "pre_active_rate": None,
            "post_active_rate": None,
            "pre_posting_rate": None,
            "post_posting_rate": None,
            "lift_pct": None,
            "final_week_retention_rate": None,
            "avg_pre_posts_per_week": None,
            "avg_post_posts_per_week": None,
            "avg_pre_sales_per_week": None,
            "avg_post_sales_per_week": None,
        }

    pre = daily_offsets[daily_offsets["period"] == "pre"]
    post = daily_offsets[daily_offsets["period"] == "post"]

    pre_active_rate = pre["active"].mean() * 100 if not pre.empty else None
    post_active_rate = post["active"].mean() * 100 if not post.empty else None
    pre_posting_rate = (
        pre.groupby("creator_id")["posts"].sum().gt(0).mean() * 100 if not pre.empty else None
    )
    post_posting_rate = (
        post.groupby("creator_id")["posts"].sum().gt(0).mean() * 100 if not post.empty else None
    )
    lift_pct = (
        ((post_active_rate - pre_active_rate) / pre_active_rate) * 100
        if pre_active_rate not in (None, 0) and post_active_rate is not None
        else None
    )

    final_week_start_offset = window_days - 6
    final_week = post[post["offset"] >= final_week_start_offset]
    if not final_week.empty:
        still_active = final_week.groupby("creator_id")["active"].any()
        final_week_retention_rate = still_active.mean() * 100
    else:
        final_week_retention_rate = None

    def per_week(df: pd.DataFrame, column: str) -> float | None:
        if df.empty:
            return None
        n_creators = df["creator_id"].nunique()
        n_weeks = window_days / 7
        return df[column].sum() / n_creators / n_weeks if n_creators else None

    return {
        "n_creators": daily_offsets["creator_id"].nunique(),
        "pre_active_rate": pre_active_rate,
        "post_active_rate": post_active_rate,
        "pre_posting_rate": pre_posting_rate,
        "post_posting_rate": post_posting_rate,
        "lift_pct": lift_pct,
        "final_week_retention_rate": final_week_retention_rate,
        "avg_pre_posts_per_week": per_week(pre, "posts"),
        "avg_post_posts_per_week": per_week(post, "posts"),
        "avg_pre_sales_per_week": per_week(pre, "sales"),
        "avg_post_sales_per_week": per_week(post, "sales"),
    }
