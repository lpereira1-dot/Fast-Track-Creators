"""Creator lifecycle email reminders: welcome, post/sale nudges, and a

first-sale congratulations -- sent via CreatorIQ's bulk-communication
endpoint (see `api.creatoriq.EmailSender` / `CreatorIQClient.send_bulk_email`).

Rules (as specified by the program owner):
  - Email 1 (welcome/bonus intro): once, as soon as possible after joining.
  - Email 2 (posted, no sale yet): fires as soon as a first post is
    detected, then repeats every `reminder_interval_days` until a
    qualifying sale happens or the activation window closes.
  - Email 3 (no post yet): starts on `post_reminder_start_day`, then
    repeats every `reminder_interval_days` until a post happens or the
    activation window closes.
  - Email 4 (first-sale congrats): once, as soon as a *qualifying* first
    sale is detected (i.e. within the activation window) -- takes priority
    over the reminders that run, since a creator who just succeeded
    doesn't need to also be nagged the same day.

`CreatorEmailConfig.min_join_date`, if set, excludes anyone who joined
before that date from ALL four emails -- so launching this feature after
creators have already been in the program a while doesn't trigger a
"catch-up" batch of now-stale-feeling welcomes/reminders for them.

Designed to run daily (see `.github/workflows/creator-emails.yml`), driven
off `StateStore.all_creators()` (populated by the weekly cohort job) and
each creator's current activation status (`ReportsClient.fetch_activation`),
so it never re-fetches the campaign roster itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from fast_track.api.creatoriq import EmailSender, ReportsClient
from fast_track.config import Settings
from fast_track.emails import templates
from fast_track.models import ActivationRecord, Creator, Milestone
from fast_track.storage.state_store import StateStore
from fast_track.workflow.eligibility import evaluate_awards

logger = logging.getLogger(__name__)

WELCOME = "welcome"
SALE_REMINDER = "sale_reminder"
POST_REMINDER = "post_reminder"
SALE_CONGRATS = "sale_congrats"


@dataclass
class CreatorEmailJobResult:
    creators_checked: int
    sent: list[tuple[Creator, str]] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        lines = [f"Checked {self.creators_checked} creator(s) for lifecycle emails."]
        verb = "would send" if self.dry_run else "sent"
        lines.append(f"{len(self.sent)} email(s) {verb}.")
        for creator, email_type in self.sent:
            lines.append(f"  - {creator.name} <{creator.email}>: {email_type}")
        return "\n".join(lines)


def _due_for_repeat(
    store: StateStore, creator_id: str, email_type: str, today: date, interval_days: int
) -> bool:
    last_sent = store.last_email_sent_at(creator_id, email_type)
    return last_sent is None or (today - last_sent).days >= interval_days


def _plan_emails_for_creator(
    creator: Creator,
    activation: ActivationRecord | None,
    store: StateStore,
    settings: Settings,
    today: date,
) -> list[tuple[str, str, str]]:
    """Returns [(email_type, subject, body), ...] this creator is due for right now."""

    cfg = settings.creator_email
    rules = settings.program
    joined_date = creator.joined_at.date()

    if cfg.min_join_date is not None and joined_date < cfg.min_join_date:
        return []  # joined before this feature's cutoff -- no catch-up batch for pre-existing creators

    days_since_joined = (today - joined_date).days
    if not (0 <= days_since_joined <= rules.activation_window_days):
        return []  # outside the program window -- no lifecycle emails relevant anymore

    has_posted = bool(activation and activation.first_post_at is not None)
    qualifying_milestones = (
        {a.milestone for a in evaluate_awards(creator, activation, rules)} if activation else set()
    )
    has_qualifying_sale = Milestone.FIRST_SALE in qualifying_milestones

    planned: list[tuple[str, str, str]] = []

    # Email 4 takes priority -- a creator who just succeeded doesn't also
    # need a reminder nudge the same run.
    if has_qualifying_sale and store.last_email_sent_at(creator.creator_id, SALE_CONGRATS) is None:
        subject, body = templates.sale_congrats_email(cfg, today)
        return [(SALE_CONGRATS, subject, body)]

    if store.last_email_sent_at(creator.creator_id, WELCOME) is None:
        subject, body = templates.welcome_email(cfg)
        planned.append((WELCOME, subject, body))

    if has_posted and not has_qualifying_sale:
        if _due_for_repeat(store, creator.creator_id, SALE_REMINDER, today, cfg.reminder_interval_days):
            subject, body = templates.sale_reminder_email(cfg)
            planned.append((SALE_REMINDER, subject, body))

    if not has_posted and days_since_joined >= cfg.post_reminder_start_day:
        if _due_for_repeat(store, creator.creator_id, POST_REMINDER, today, cfg.reminder_interval_days):
            subject, body = templates.post_reminder_email(cfg, today)
            planned.append((POST_REMINDER, subject, body))

    return planned


def run_creator_email_job(
    reports_client: ReportsClient,
    email_sender: EmailSender,
    store: StateStore,
    settings: Settings,
    today: date | None = None,
    dry_run: bool = False,
) -> CreatorEmailJobResult:
    today = today or date.today()

    creators = store.all_creators()
    activation_records = reports_client.fetch_activation([c.creator_id for c in creators])
    activations_by_id = {r.creator_id: r for r in activation_records}

    # (email_type, subject, body) -> [creators] -- grouped so each email
    # type is sent as a single real bulk call rather than one per creator.
    groups: dict[str, list[Creator]] = {}
    content_by_type: dict[str, tuple[str, str]] = {}
    for creator in creators:
        activation = activations_by_id.get(creator.creator_id)
        for email_type, subject, body in _plan_emails_for_creator(
            creator, activation, store, settings, today
        ):
            groups.setdefault(email_type, []).append(creator)
            content_by_type[email_type] = (subject, body)

    sent: list[tuple[Creator, str]] = []
    if dry_run:
        for email_type, recipients in groups.items():
            sent.extend((creator, email_type) for creator in recipients)
    else:
        for email_type, recipients in groups.items():
            subject, body = content_by_type[email_type]
            try:
                email_sender.send_bulk_email(subject, body, [c.creator_id for c in recipients])
            except Exception:
                logger.exception(
                    "Failed to send %r to %d creator(s) -- will retry next run",
                    email_type,
                    len(recipients),
                )
                continue
            for creator in recipients:
                store.record_email_sent(creator.creator_id, email_type, today)
                sent.append((creator, email_type))

    return CreatorEmailJobResult(creators_checked=len(creators), sent=sent, dry_run=dry_run)
