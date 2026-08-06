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
    retention_curve,
    summarize_retention,
)
from fast_track.storage.state_store import StateStore  # noqa: E402

st.set_page_config(page_title="Fast Track Creators - Gift Card Retention", layout="wide")


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
        load_data.clear()  # don't let a stale cached read shadow the freshly-synced file
        return "✅ Synced with the latest GitHub Actions run."
    return "ℹ️ No GitHub Actions data artifact found yet -- showing local data only."


@st.cache_data(ttl=300)
def load_data(db_path: str):
    store = StateStore(db_path)
    try:
        awards = store.all_awards()
        activity = store.get_activity()
    finally:
        store.close()
    return awards, activity


def fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def fmt_num(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def main() -> None:
    settings = get_settings()
    st.title("🎁 Fast Track Creators — Gift Card Activation & Retention")
    st.caption(
        "$25 gift card for a creator's first post within "
        f"{settings.program.activation_window_days} days of joining, plus $25 for their first sale. "
        f"This dashboard compares creator activity {settings.program.retention_window_days} days "
        "before vs. after they received a gift card."
    )

    sync_status = sync_db_from_github(settings.storage.db_path)
    if sync_status:
        st.caption(sync_status)

    awards, activity = load_data(settings.storage.db_path)

    if not awards:
        st.info(
            "No gift awards recorded yet. Run the weekly cohort job "
            "(`fast-track run-weekly-job`) to pull activation data from CreatorIQ and "
            "populate this dashboard, or `python scripts/generate_fixtures.py` + "
            "`CREATORIQ_USE_FIXTURES=true fast-track run-weekly-job` for a demo."
        )
        return

    gift_events = build_gift_events(awards)

    with st.sidebar:
        st.header("Filters")
        window_days = st.slider(
            "Retention window (days pre/post gift)",
            min_value=7,
            max_value=60,
            value=settings.program.retention_window_days,
            step=1,
        )
        milestone_options = sorted(
            {m.strip() for row in gift_events["milestones"] for m in row.split(",")}
        )
        selected_milestones = st.multiselect(
            "Milestone", options=milestone_options, default=milestone_options
        )
        cohort_weeks = sorted(
            {pd.Timestamp(d).to_period("W-MON").start_time.date() for d in gift_events["joined_at"]}
        )
        selected_cohorts = st.multiselect(
            "Cohort week (joined)", options=cohort_weeks, default=cohort_weeks
        )

    filtered_events = gift_events[
        gift_events["milestones"].apply(
            lambda row: any(m.strip() in selected_milestones for m in row.split(","))
        )
        & gift_events["joined_at"].apply(
            lambda d: pd.Timestamp(d).to_period("W-MON").start_time.date() in selected_cohorts
        )
    ]

    if filtered_events.empty:
        st.warning("No creators match the selected filters.")
        return

    daily_offsets = build_daily_offsets(filtered_events, activity, window_days)
    summary = summarize_retention(daily_offsets, window_days)
    curve = retention_curve(daily_offsets)

    st.subheader("Summary")
    cols = st.columns(6)
    cols[0].metric("Gifted creators", summary["n_creators"])
    cols[1].metric("Pre-gift active rate", fmt_pct(summary["pre_active_rate"]))
    cols[2].metric("Post-gift active rate", fmt_pct(summary["post_active_rate"]))
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
    rate_cols[0].metric("Avg posts/week (pre)", fmt_num(summary["avg_pre_posts_per_week"]))
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
                line=dict(color="#2563eb"),
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
                lambda d: pd.Timestamp(d).to_period("W-MON").start_time.date()
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
                    "pre_posts": int(df[df["period"] == "pre"]["posts"].sum()),
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
        .sort_values("gift_date")
    )
    st.dataframe(per_creator, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
