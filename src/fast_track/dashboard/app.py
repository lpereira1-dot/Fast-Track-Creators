"""Streamlit dashboard: pre/post gift-card activity & retention.

Run with:

    streamlit run src/fast_track/dashboard/app.py

Reads from the local state store (populated by the weekly cohort job +
daily activity sync job), so it works offline against demo/fixture data or
against real CreatorIQ-sourced data once the scheduled jobs have run.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow `streamlit run src/fast_track/dashboard/app.py` without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# When deployed on Streamlit Community Cloud, credentials are configured via
# its "Secrets" manager (st.secrets), not real OS environment variables --
# but the rest of this app (config.py) reads everything via os.getenv(), so
# it works unchanged locally, in GitHub Actions, or on Streamlit Cloud.
# Bridge one into the other here, before any fast_track module (which reads
# env vars at import/construction time) is imported. Accessing `st.secrets`
# raises `StreamlitSecretNotFoundError` when no secrets.toml exists at all
# (the normal case locally/in CLI use), so this has to be guarded rather
# than just an empty-by-default lookup.
try:
    _secrets = dict(st.secrets)
except Exception:  # noqa: BLE001 -- no secrets.toml configured; nothing to bridge
    _secrets = {}
for _key, _value in _secrets.items():
    os.environ.setdefault(_key, str(_value))

from fast_track.config import get_settings  # noqa: E402
from fast_track.dashboard.db_sync import sync_db_from_github_artifact  # noqa: E402
from fast_track.dashboard.metrics import (  # noqa: E402
    build_daily_offsets,
    build_gift_events,
    coerce_date,
    direct_pre_gift_metrics,
    first_post_date_by_creator,
    merge_activity_records,
    milestone_activity_records,
    retention_curve,
    summarize_retention,
)
from fast_track.models import Creator, GiftAward, Milestone  # noqa: E402
from fast_track.storage.state_store import StateStore  # noqa: E402

st.set_page_config(page_title="Fast Track Creators - Gift Card Retention", layout="wide")

# Bumped when dashboard behavior changes -- visible in the sidebar so you
# can confirm Streamlit Cloud picked up a new deploy after merging.
_DASHBOARD_BUILD = "2026-09-02-v8"


@st.cache_data(ttl=300)
def sync_db_from_github(db_path: str) -> str | None:
    """Best-effort refresh from the latest GitHub Actions artifact.

    The scheduled weekly-cohort/daily-activity-sync jobs run in GitHub
    Actions, not wherever this dashboard happens to be hosted -- if
    they're on separate infrastructure (e.g. this deployed on Streamlit
    Community Cloud), this is what keeps the dashboard showing current
    data instead of nothing. Set GITHUB_REPO ("owner/repo") and a
    GITHUB_TOKEN with `actions:read` access as secrets/env vars to enable
    it; without them, the dashboard just reads whatever's already at
    FAST_TRACK_DB_PATH (fine for local use alongside the CLI). Returns a
    status message to display, or None if sync wasn't configured.
    """

    repo = os.getenv("GITHUB_REPO")
    token = os.getenv("GITHUB_TOKEN")
    if not repo or not token:
        return None
    try:
        synced = sync_db_from_github_artifact(repo, token, db_path)
    except Exception as exc:  # noqa: BLE001 -- never let a sync hiccup crash the page
        return f"⚠️ Could not sync from GitHub Actions ({exc}); showing local data only."
    if synced:
        # Don't let a stale cached read shadow the freshly-synced file.
        load_data.clear()
        load_email_log.clear()
        load_creators.clear()
        load_activation_data.clear()
        return "✅ Synced with the latest GitHub Actions run."
    return "ℹ️ No GitHub Actions data artifact found yet -- showing local data only."


@st.cache_data(ttl=300)
def load_creators(db_path: str) -> list[Creator]:
    store = StateStore(db_path)
    try:
        return store.all_creators()
    finally:
        store.close()


@st.cache_data(ttl=300)
def load_data(db_path: str):
    store = StateStore(db_path)
    try:
        awards = store.all_awards()
        activity = store.get_activity()
    finally:
        store.close()
    return awards, activity


@st.cache_data(ttl=300)
def load_activation_data(db_path: str):
    store = StateStore(db_path)
    try:
        first_posts = store.all_first_post_observations()
        awards = store.all_awards()
        activity = store.get_activity()
    finally:
        store.close()

    awards_by_creator: dict[str, list[GiftAward]] = {}
    for award in awards:
        awards_by_creator.setdefault(award.creator.creator_id, []).append(award)

    first_sale_dates: dict[str, date] = {}
    for creator_id, creator_awards in awards_by_creator.items():
        sale_awards = [a for a in creator_awards if a.milestone is Milestone.FIRST_SALE]
        if sale_awards:
            first_sale_dates[creator_id] = min(a.completed_at.date() for a in sale_awards)

    for record in activity:
        if record.sales <= 0:
            continue
        existing = first_sale_dates.get(record.creator_id)
        if existing is None or record.activity_date < existing:
            first_sale_dates[record.creator_id] = record.activity_date

    return first_posts, awards_by_creator, first_sale_dates


@st.cache_data(ttl=300)
def load_email_log(db_path: str):
    store = StateStore(db_path)
    try:
        return store.all_creator_emails()
    finally:
        store.close()


def _cohort_week_start(joined: date, week_start_weekday: int) -> date:
    """Bucket a join date into its cohort week (matches `Creator.cohort_week_start`)."""

    target = week_start_weekday - 1
    delta_days = (joined.weekday() - target) % 7
    return joined - timedelta(days=delta_days)


def _build_cohort_index(
    creators: list[Creator], week_start_weekday: int
) -> tuple[dict[str, date], dict[date, list[Creator]]]:
    """Map creator_id -> cohort week, and cohort week -> creators in that week."""

    by_creator: dict[str, date] = {}
    by_week: dict[date, list[Creator]] = {}
    for creator in creators:
        week = creator.cohort_week_start(week_start_weekday)
        by_creator[creator.creator_id] = week
        by_week.setdefault(week, []).append(creator)
    return by_creator, by_week


def _default_cohort_weeks(
    cohort_weeks: list[date],
    awards: list,
    week_start_weekday: int,
) -> list[date]:
    """Pick a sensible default cohort filter for the retention section.

    The current roster week often has no gift awards yet (weekly job runs on
    Tuesdays), so default to the most recent cohort week that already has at
    least one gifted creator.
    """

    if not cohort_weeks:
        return []

    gifted_weeks = sorted(
        {
            award.creator.cohort_week_start(week_start_weekday)
            for award in awards
        }
        & set(cohort_weeks)
    )
    current_week = _cohort_week_start(date.today(), week_start_weekday)
    if current_week in gifted_weeks:
        return [current_week]
    if gifted_weeks:
        return [gifted_weeks[-1]]
    return [cohort_weeks[-1]]
    return "-" if value is None else f"{value:.1f}%"


def fmt_num(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


_EMAIL_TYPE_LABELS = {
    "welcome": "Welcome",
    "post_reminder": "Post reminder",
    "sale_reminder": "Sale reminder",
    "sale_congrats": "Sale congrats",
}


def render_cohort_activation_status(
    cohort_creators: list[Creator],
    first_posts: dict[str, date],
    awards_by_creator: dict[str, list[GiftAward]],
    first_sale_dates: dict[str, date],
    email_log: list,
) -> None:
    """Per-creator activation progress for the selected cohort week.

    Sale-reminder emails fire when a first post is observed locally
    (`first_post_observations`) -- that signal is separate from the
    retention dashboard's daily post counts, which CreatorIQ does not
    expose as a per-day history.
    """

    st.subheader("Cohort activation status")
    if not cohort_creators:
        st.caption("No creators in the selected cohort week.")
        return

    emails_by_creator: dict[str, list[str]] = {}
    for entry in email_log:
        label = _EMAIL_TYPE_LABELS.get(entry.email_type, entry.email_type)
        emails_by_creator.setdefault(entry.creator_id, []).append(label)

    rows = []
    posted_count = 0
    sale_count = 0
    for creator in sorted(cohort_creators, key=lambda c: c.joined_at):
        first_post = first_posts.get(creator.creator_id)
        posted = first_post is not None
        if posted:
            posted_count += 1

        first_sale = first_sale_dates.get(creator.creator_id)
        if first_sale is not None:
            sale_count += 1

        creator_awards = awards_by_creator.get(creator.creator_id, [])
        gift_labels = sorted({a.milestone.label for a in creator_awards})

        rows.append(
            {
                "Creator": creator.name,
                "Admitted": creator.joined_at.date(),
                "Posted": "Yes" if posted else "No",
                "First post observed": first_post or "—",
                "First sale": "Yes" if first_sale else "No",
                "First sale date": first_sale or "—",
                "Gift milestones": ", ".join(gift_labels) if gift_labels else "—",
                "Emails sent": ", ".join(sorted(set(emails_by_creator.get(creator.creator_id, [])))) or "—",
            }
        )

    summary = st.columns(4)
    summary[0].metric("Creators in cohort", len(cohort_creators))
    summary[1].metric("Posted", posted_count)
    summary[2].metric("First sale", sale_count)
    summary[3].metric(
        "Sale reminders",
        sum(1 for e in email_log if e.email_type == "sale_reminder"),
    )

    st.caption(
        "Posted / first-sale status comes from CreatorIQ roster observations stored "
        "locally (`first_post_observations` and qualifying sales). The gift-retention "
        "section below tracks sales activity only — daily post counts are not available "
        "from CreatorIQ's API."
    )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_creator_email_status(email_log: list, cohort_creators: list[Creator]) -> None:
    """Send status for the creator lifecycle emails (see `workflow/creator_emails.py`).

    Deliberately send status only, not open/click rates -- CreatorIQ's
    bulk-email endpoint doesn't expose that, and rather than show a
    fabricated or misleading number, this only shows what's actually known:
    who got which email, when, and how many times (for repeating reminders).
    """

    st.subheader("Creator email status")
    if not cohort_creators:
        st.caption("No creators in the selected cohort week.")
        return

    cohort_ids = {c.creator_id for c in cohort_creators}
    email_log = [entry for entry in email_log if entry.creator_id in cohort_ids]

    if not email_log:
        st.caption(
            f"{len(cohort_creators)} creator(s) in this cohort — no lifecycle emails sent yet."
        )
        roster = pd.DataFrame(
            [{"Creator": c.name, "Email": c.email, "Admitted": c.joined_at.date()} for c in cohort_creators]
        ).sort_values("Admitted")
        st.dataframe(roster, use_container_width=True, hide_index=True)
        return

    st.caption(
        "Send status only \u2014 CreatorIQ's bulk-email endpoint doesn't expose open/click "
        "tracking, so this shows who received what email and when, not whether they opened it."
    )

    creators_by_type: dict[str, set[str]] = {}
    for entry in email_log:
        creators_by_type.setdefault(entry.email_type, set()).add(entry.creator_id)

    cols = st.columns(2 + len(_EMAIL_TYPE_LABELS))
    cols[0].metric("Creators emailed", len({e.creator_id for e in email_log}))
    cols[1].metric("Total sends (incl. repeats)", sum(e.send_count for e in email_log))
    for i, (email_type, label) in enumerate(_EMAIL_TYPE_LABELS.items(), start=2):
        cols[i].metric(label, len(creators_by_type.get(email_type, set())))

    table = pd.DataFrame(
        [
            {
                "Creator": e.creator_name,
                "Email": e.creator_email,
                "Email type": _EMAIL_TYPE_LABELS.get(e.email_type, e.email_type),
                "Last sent": e.last_sent_at,
                "Times sent": e.send_count,
            }
            for e in email_log
        ]
    ).sort_values("Last sent", ascending=False)
    st.dataframe(table, use_container_width=True, hide_index=True)


def main() -> None:
    settings = get_settings()
    week_start_weekday = settings.program.cohort_week_start_weekday
    st.title("🎁 Fast Track Creators — Gift Card Activation & Retention")
    st.caption(
        f"Build {_DASHBOARD_BUILD} · $25 gift card for a creator's first post within "
        f"{settings.program.activation_window_days} days of joining, plus $25 for their first sale. "
        f"This dashboard compares creator activity {settings.program.retention_window_days} days "
        "before vs. after they received a gift card."
    )

    sync_status = sync_db_from_github(settings.storage.db_path)
    github_configured = bool(os.getenv("GITHUB_REPO") and os.getenv("GITHUB_TOKEN"))

    creators = load_creators(settings.storage.db_path)
    awards, activity = load_data(settings.storage.db_path)
    email_log = load_email_log(settings.storage.db_path)
    first_posts, awards_by_creator, first_sale_dates = load_activation_data(
        settings.storage.db_path
    )

    with st.sidebar:
        st.caption(f"Build {_DASHBOARD_BUILD}")
        if st.button("Refresh data", help="Re-download the latest database from GitHub Actions"):
            sync_db_from_github.clear()
            load_creators.clear()
            load_data.clear()
            load_email_log.clear()
            load_activation_data.clear()
            st.rerun()

    if not github_configured:
        st.warning(
            "GitHub sync is not configured. Add `GITHUB_REPO` and `GITHUB_TOKEN` "
            "(Actions: Read-only) in Streamlit **Settings -> Secrets** so this "
            "dashboard can download the latest roster and email log from GitHub "
            "Actions. Without them, the cohort filter and email status will stay empty."
        )
    elif sync_status:
        st.caption(sync_status)

    if github_configured and creators:
        st.caption(
            f"Roster: {len(creators)} creator(s) · Email log: {len(email_log)} send record(s)"
        )

    _cohort_by_creator, cohorts_by_week = _build_cohort_index(creators, week_start_weekday)
    cohort_weeks = sorted(cohorts_by_week.keys())
    current_cohort_week = _cohort_week_start(date.today(), week_start_weekday)

    if cohort_weeks:
        default_cohorts = _default_cohort_weeks(cohort_weeks, awards, week_start_weekday)

        def _format_cohort_week(week_start: date) -> str:
            count = len(cohorts_by_week.get(week_start, []))
            label = week_start.strftime("%b %d, %Y")
            if week_start == current_cohort_week:
                label = f"This week ({label})"
            return f"{label} — {count} creator(s)"

        cohort_col, _spacer = st.columns([2, 3])
        with cohort_col:
            selected_cohorts = st.multiselect(
                "Cohort week (admitted)",
                options=cohort_weeks,
                default=default_cohorts,
                format_func=_format_cohort_week,
                help="Filter email status and gift retention to creators admitted in the selected week(s).",
            )
    else:
        selected_cohorts = []
        if github_configured:
            st.info(
                "No creators in the roster yet. Run the weekly cohort job (Tuesdays) "
                "or click **Refresh data** in the sidebar after it completes."
            )
        else:
            st.info("Configure GitHub sync in Streamlit Secrets to load the creator roster.")

    with st.sidebar:
        st.header("Filters")
        if cohort_weeks:
            st.caption(
                f"Viewing {sum(len(cohorts_by_week.get(w, [])) for w in selected_cohorts)} "
                f"creator(s) across {len(selected_cohorts)} selected cohort week(s)."
            )
        else:
            st.caption("No cohort weeks available yet.")

        window_days = st.slider(
            "Retention window (days pre/post gift)",
            min_value=7,
            max_value=60,
            value=settings.program.retention_window_days,
            step=1,
        )

    selected_creator_ids = {
        creator.creator_id
        for week in selected_cohorts
        for creator in cohorts_by_week.get(week, [])
    }
    cohort_creators = [
        creator for creator in creators if creator.creator_id in selected_creator_ids
    ]
    cohort_email_log = [entry for entry in email_log if entry.creator_id in selected_creator_ids]
    render_cohort_activation_status(
        cohort_creators,
        first_posts,
        awards_by_creator,
        first_sale_dates,
        cohort_email_log,
    )
    render_creator_email_status(email_log, cohort_creators)

    if not awards:
        if cohort_creators:
            st.info(
                "No gift awards recorded for the selected cohort yet. Gift-card rows "
                "appear here once creators hit their first-post or first-sale milestones "
                "(after the weekly cohort job runs on Tuesdays)."
            )
        else:
            st.info(
                "No creators in the roster yet. Run the weekly cohort job "
                "(`fast-track run-weekly-job`) to pull activation data from CreatorIQ, "
                "or `python scripts/generate_fixtures.py` + "
                "`CREATORIQ_USE_FIXTURES=true fast-track run-weekly-job` for a demo."
            )
        return

    gift_events = build_gift_events(awards)

    with st.sidebar:
        milestone_options = sorted(
            {m.strip() for row in gift_events["milestones"] for m in row.split(",")}
        )
        selected_milestones = st.multiselect(
            "Milestone", options=milestone_options, default=milestone_options
        )

    filtered_events = gift_events[
        gift_events["creator_id"].isin(selected_creator_ids)
        & gift_events["milestones"].apply(
            lambda row: any(m.strip() in selected_milestones for m in row.split(","))
        )
    ]

    if filtered_events.empty:
        st.warning(
            "No gifted creators in the selected cohort week yet. "
            "Try selecting a different cohort week, or check back after milestones are hit."
        )
        return

    cohort_awards = [a for a in awards if a.creator.creator_id in selected_creator_ids]
    cohort_first_posts = {
        cid: day for cid, day in first_posts.items() if cid in selected_creator_ids
    }

    retention_activity = merge_activity_records(
        list(activity)
        + milestone_activity_records(cohort_awards, cohort_first_posts)
    )
    daily_offsets = build_daily_offsets(filtered_events, retention_activity, window_days)
    summary = summarize_retention(daily_offsets, window_days)
    summary.update(direct_pre_gift_metrics(filtered_events, cohort_awards, cohort_first_posts))
    curve = retention_curve(daily_offsets)
    first_post_map = first_post_date_by_creator(cohort_awards, cohort_first_posts)

    st.subheader("Gift retention (pre/post gift card)")
    st.caption(
        "Pre-gift = before the gift order was queued on the sheet (day after the "
        "weekly sync). A creator's qualifying first post counts in pre-gift even when "
        "it happened the same day as the sheet sync. Sales/GMV come from CreatorIQ "
        "transaction history; posts are shown on the first-post date only."
    )

    st.markdown("**Summary**")
    cols = st.columns(6)
    cols[0].metric("Gifted creators", summary["n_creators"])
    cols[1].metric(
        "Posted before gift",
        fmt_pct(summary["pre_posting_rate"]),
        delta=(
            None
            if summary["n_creators"] == 0
            else f"{summary['pre_gift_posters']} of {summary['n_creators']} creators"
        ),
        help="Creators whose first post was before the gift order date (day after sheet sync).",
    )
    cols[2].metric(
        "Active post-gift (daily avg)",
        fmt_pct(summary["post_active_rate"]),
        help="Average share of post-gift days with any post or sale activity.",
    )
    cols[3].metric(
        "Activity lift",
        fmt_pct(summary["lift_pct"]),
        delta=None if summary["lift_pct"] is None else f"{summary['lift_pct']:.1f}%",
    )
    cols[4].metric(
        f"Day {window_days - 6}-{window_days} retention",
        fmt_pct(summary["final_week_retention_rate"]),
        help=(
            "Share of gifted creators who posted or sold at least once in "
            "the final week of the post-gift window."
        ),
    )
    cols[5].metric(
        "Total gift spend",
        f"${filtered_events['total_gift_usd'].sum():,.0f}",
    )

    rate_cols = st.columns(4)
    rate_cols[0].metric(
        "Pre-gift posts per creator",
        fmt_num(summary["avg_pre_posts_per_creator"]),
        help="Average qualifying first posts per gifted creator before the gift order date.",
    )
    rate_cols[1].metric("Avg posts/week (post)", fmt_num(summary["avg_post_posts_per_week"]))
    rate_cols[2].metric("Avg sales/week (pre)", fmt_num(summary["avg_pre_sales_per_week"]))
    rate_cols[3].metric("Avg sales/week (post)", fmt_num(summary["avg_post_sales_per_week"]))

    st.subheader("Retention curve: % of creators active, by day relative to gift")
    if not curve.empty:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=curve["offset"],
                y=curve["pct_active"],
                mode="lines+markers",
                name="% active",
                line=dict(color="#7F187F"),  # Wayfair brand purple
            )
        )
        fig.add_vline(x=0, line_dash="dash", line_color="gray", annotation_text="Gift received")
        fig.update_layout(
            xaxis_title="Days relative to gift card",
            yaxis_title="% of creators active that day",
            yaxis_range=[0, 100],
            height=420,
        )
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Cohort breakdown")
    cohort_summary = (
        filtered_events.assign(
            cohort_week=lambda df: df["joined_at"].apply(
                lambda d: _cohort_week_start(d, week_start_weekday)
            )
        )
        .groupby("cohort_week")
        .agg(gifted_creators=("creator_id", "nunique"), total_gift_usd=("total_gift_usd", "sum"))
        .reset_index()
        .sort_values("cohort_week")
    )
    st.dataframe(cohort_summary, use_container_width=True, hide_index=True)

    st.subheader("Creator detail")
    per_creator = (
        daily_offsets.groupby(["creator_id", "name"])
        .apply(
            lambda df: pd.Series(
                {
                    "pre_active_days": int(df[df["period"] == "pre"]["active"].sum()),
                    "post_active_days": int(df[df["period"] == "post"]["active"].sum()),
                    "post_posts": int(df[df["period"] == "post"]["posts"].sum()),
                    "pre_sales": int(df[df["period"] == "pre"]["sales"].sum()),
                    "post_sales": int(df[df["period"] == "post"]["sales"].sum()),
                }
            ),
            include_groups=False,
        )
        .reset_index()
        .merge(
            filtered_events[["creator_id", "email", "gift_date", "milestones", "total_gift_usd"]],
            on="creator_id",
        )
    )
    per_creator["pre_posts"] = per_creator.apply(
        lambda row: int(
            (fp := first_post_map.get(row["creator_id"])) is not None
            and fp < coerce_date(row["gift_date"])
        ),
        axis=1,
    )
    per_creator = per_creator.sort_values("gift_date")
    st.dataframe(per_creator, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
