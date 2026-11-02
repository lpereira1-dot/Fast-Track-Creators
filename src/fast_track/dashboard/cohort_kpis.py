"""Cohort KPI snapshots for month-over-month dashboard comparisons."""

from __future__ import annotations

from datetime import date

import pandas as pd

from fast_track.dashboard.metrics import build_gift_events, direct_pre_gift_metrics
from fast_track.models import GiftAward, Creator


def previous_month_start(month_start: date) -> date:
    if month_start.month == 1:
        return date(month_start.year - 1, 12, 1)
    return date(month_start.year, month_start.month - 1, 1)


def cohort_month_kpis(
    creator_ids: set[str],
    creators: list[Creator],
    first_posts: dict[str, date],
    first_sale_dates: dict[str, date],
    awards: list[GiftAward],
) -> dict[str, float | int | None]:
    """Headline metrics for creators admitted in a calendar month."""

    if not creator_ids:
        return {
            "n_creators": 0,
            "n_posted": 0,
            "post_rate": None,
            "n_first_sale": 0,
            "sale_rate": None,
            "n_gifted": 0,
            "gift_spend": 0.0,
            "pre_posting_rate": None,
        }

    cohort = [c for c in creators if c.creator_id in creator_ids]
    n_creators = len(cohort)
    n_posted = sum(1 for c in cohort if c.creator_id in first_posts)
    n_first_sale = sum(1 for c in cohort if c.creator_id in first_sale_dates)

    month_awards = [a for a in awards if a.creator.creator_id in creator_ids]
    gift_events = build_gift_events(month_awards)
    n_gifted = int(gift_events["creator_id"].nunique()) if not gift_events.empty else 0
    gift_spend = float(gift_events["total_gift_usd"].sum()) if not gift_events.empty else 0.0

    first_post_subset = {cid: day for cid, day in first_posts.items() if cid in creator_ids}
    pre = direct_pre_gift_metrics(gift_events, month_awards, first_post_subset)

    return {
        "n_creators": n_creators,
        "n_posted": n_posted,
        "post_rate": (n_posted / n_creators) * 100 if n_creators else None,
        "n_first_sale": n_first_sale,
        "sale_rate": (n_first_sale / n_creators) * 100 if n_creators else None,
        "n_gifted": n_gifted,
        "gift_spend": gift_spend,
        "pre_posting_rate": pre["pre_posting_rate"],
    }


def format_count_delta(current: int, prior: int) -> str:
    if prior == 0:
        return f"+{current}" if current else "—"
    delta = current - prior
    pct = (delta / prior) * 100
    return f"{delta:+d} ({pct:+.1f}%)"


def format_rate_delta(current: float | None, prior: float | None) -> str:
    if current is None or prior is None:
        return "—"
    return f"{current - prior:+.1f} pp"


def format_currency_delta(current: float, prior: float) -> str:
    delta = current - prior
    if prior == 0:
        return f"${delta:+,.0f}" if current else "—"
    pct = (delta / prior) * 100
    return f"${delta:+,.0f} ({pct:+.1f}%)"


def month_over_month_table(
    prior_label: str,
    current_label: str,
    prior: dict[str, float | int | None],
    current: dict[str, float | int | None],
) -> pd.DataFrame:
    rows = [
        {
            "Metric": "Creators admitted",
            prior_label: prior["n_creators"],
            current_label: current["n_creators"],
            "Change": format_count_delta(int(current["n_creators"]), int(prior["n_creators"])),
        },
        {
            "Metric": "Posted (count)",
            prior_label: prior["n_posted"],
            current_label: current["n_posted"],
            "Change": format_count_delta(int(current["n_posted"]), int(prior["n_posted"])),
        },
        {
            "Metric": "Post rate",
            prior_label: None if prior["post_rate"] is None else f"{prior['post_rate']:.1f}%",
            current_label: None if current["post_rate"] is None else f"{current['post_rate']:.1f}%",
            "Change": format_rate_delta(
                current["post_rate"] if isinstance(current["post_rate"], (int, float)) else None,
                prior["post_rate"] if isinstance(prior["post_rate"], (int, float)) else None,
            ),
        },
        {
            "Metric": "First sale (count)",
            prior_label: prior["n_first_sale"],
            current_label: current["n_first_sale"],
            "Change": format_count_delta(int(current["n_first_sale"]), int(prior["n_first_sale"])),
        },
        {
            "Metric": "Gifted creators",
            prior_label: prior["n_gifted"],
            current_label: current["n_gifted"],
            "Change": format_count_delta(int(current["n_gifted"]), int(prior["n_gifted"])),
        },
        {
            "Metric": "Gift spend",
            prior_label: f"${prior['gift_spend']:,.0f}",
            current_label: f"${current['gift_spend']:,.0f}",
            "Change": format_currency_delta(float(current["gift_spend"]), float(prior["gift_spend"])),
        },
        {
            "Metric": "Posted before gift (gifted)",
            prior_label: None if prior["pre_posting_rate"] is None else f"{prior['pre_posting_rate']:.1f}%",
            current_label: None
            if current["pre_posting_rate"] is None
            else f"{current['pre_posting_rate']:.1f}%",
            "Change": format_rate_delta(
                current["pre_posting_rate"]
                if isinstance(current["pre_posting_rate"], (int, float))
                else None,
                prior["pre_posting_rate"]
                if isinstance(prior["pre_posting_rate"], (int, float))
                else None,
            ),
        },
    ]
    return pd.DataFrame(rows)
