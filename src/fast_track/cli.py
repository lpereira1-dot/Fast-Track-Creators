"""Command-line entrypoint for the Fast Track creator gift-card workflow.

    fast-track run-weekly-job [--dry-run]     # pull new cohorts, sync gift-sheet
    fast-track sync-activity                  # refresh activity history for the dashboard
    fast-track send-creator-emails [--dry-run]  # send lifecycle reminder/congrats emails
    fast-track dashboard                      # launch the Streamlit retention dashboard
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path

from fast_track.api.creatoriq import build_reports_client
from fast_track.config import get_settings
from fast_track.sheets.gift_order_sheet import GiftOrderSheetClient
from fast_track.storage.state_store import StateStore
from fast_track.workflow.activity_sync import run_activity_sync_job
from fast_track.workflow.backfill import run_backfill_job
from fast_track.workflow.creator_emails import run_creator_email_job
from fast_track.workflow.weekly_job import run_weekly_cohort_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fast_track.cli")


def _build_sheet_client(settings) -> GiftOrderSheetClient | None:
    if not settings.sheets.has_credentials():
        return None
    return GiftOrderSheetClient(settings.sheets)


def cmd_run_weekly_job(args: argparse.Namespace) -> int:
    settings = get_settings()
    sheet_client = None if args.dry_run else _build_sheet_client(settings)

    with StateStore(settings.storage.db_path) as store:
        # `store` doubles as the "first post" observer -- CreatorIQ doesn't
        # expose a true per-post timestamp, so it's tracked locally (see
        # StateStore.resolve_first_post_dates).
        reports_client = build_reports_client(settings.creatoriq, first_post_observer=store)
        result = run_weekly_cohort_job(
            reports_client=reports_client,
            store=store,
            settings=settings,
            sheet_client=sheet_client,
            dry_run=args.dry_run,
        )
    print(result.summary())
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    settings = get_settings()
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until) if args.until else date.today()
    with StateStore(settings.storage.db_path) as store:
        reports_client = build_reports_client(settings.creatoriq, first_post_observer=store)
        result = run_backfill_job(reports_client, store, settings, since=since, until=until)
    print(result.summary())
    return 0


def cmd_sync_activity(_args: argparse.Namespace) -> int:
    settings = get_settings()
    reports_client = build_reports_client(settings.creatoriq)
    with StateStore(settings.storage.db_path) as store:
        result = run_activity_sync_job(reports_client, store, settings)
    print(result.summary())
    return 0


def cmd_send_creator_emails(args: argparse.Namespace) -> int:
    settings = get_settings()
    dry_run = args.dry_run

    # Safety gate, independent of --dry-run: even with real CreatorIQ
    # credentials configured, real sends stay off until this is explicitly
    # enabled -- see CreatorEmailConfig.sending_enabled. This is a live
    # program with real creators on the other end, so a code/config change
    # alone should never be enough to start sending for real.
    if not settings.creator_email.sending_enabled and not dry_run:
        logger.warning(
            "CREATOR_EMAIL_SENDING_ENABLED is not set to true -- forcing --dry-run. "
            "Set it explicitly once you're ready for real sends."
        )
        dry_run = True

    with StateStore(settings.storage.db_path) as store:
        reports_client = build_reports_client(settings.creatoriq, first_post_observer=store)
        result = run_creator_email_job(
            reports_client=reports_client,
            email_sender=reports_client,
            store=store,
            settings=settings,
            dry_run=dry_run,
        )
    print(result.summary())
    return 0


def cmd_dashboard(_args: argparse.Namespace) -> int:
    app_path = Path(__file__).resolve().parent / "dashboard" / "app.py"
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app_path)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fast-track", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    weekly = subparsers.add_parser(
        "run-weekly-job", help="Pull new creator cohorts and sync gift-eligible creators to the sheet."
    )
    weekly.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate eligibility and print results without writing to the Google Sheet or state store.",
    )
    weekly.set_defaults(func=cmd_run_weekly_job)

    backfill = subparsers.add_parser(
        "backfill",
        help="One-time import of pre-automation (manually-run) creators/awards for dashboard history only.",
    )
    backfill.add_argument("--since", required=True, help="Start date (YYYY-MM-DD), e.g. program launch date.")
    backfill.add_argument("--until", help="End date (YYYY-MM-DD). Defaults to today.")
    backfill.set_defaults(func=cmd_backfill)

    activity = subparsers.add_parser(
        "sync-activity", help="Refresh daily activity history used by the retention dashboard."
    )
    activity.set_defaults(func=cmd_sync_activity)

    creator_emails = subparsers.add_parser(
        "send-creator-emails",
        help="Send welcome/reminder/congrats lifecycle emails to creators via CreatorIQ.",
    )
    creator_emails.add_argument(
        "--dry-run",
        action="store_true",
        help="Print who would be emailed without actually sending or recording anything.",
    )
    creator_emails.set_defaults(func=cmd_send_creator_emails)

    dashboard = subparsers.add_parser("dashboard", help="Launch the Streamlit retention dashboard.")
    dashboard.set_defaults(func=cmd_dashboard)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
