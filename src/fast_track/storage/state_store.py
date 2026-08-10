"""SQLite-backed state store.

Tracks several things across workflow runs:

1. `creators` -- every creator we've seen, so the dashboard can look them up
   by id/email even after they've dropped off the "new creators" window.
2. `gift_awards` -- every milestone that has already been written to the
   Google Sheet, keyed by (creator_id, milestone), so re-running the weekly
   job never double-orders a gift card for the same milestone.
3. `activity` -- daily activity snapshots pulled from CreatorIQ, which feed
   the pre/post retention dashboard.
4. `first_post_observations` -- a locally-observed proxy for "first post"
   (see that table's own comment below for why this exists).
5. `creator_emails` -- which lifecycle reminder/congrats emails have been
   sent to which creators, and when, so one-time emails never resend and
   repeating reminders know when they last went out.

SQLite (stdlib, zero extra services to run) is intentionally simple here;
swap in Postgres/BigQuery later by re-implementing this class if the dataset
outgrows a single file.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from fast_track.models import ActivityRecord, Creator, GiftAward, Milestone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    creator_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    joined_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gift_awards (
    creator_id TEXT NOT NULL,
    milestone TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    completed_at TEXT NOT NULL,
    cohort_week_start TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (creator_id, milestone)
);

CREATE TABLE IF NOT EXISTS activity (
    creator_id TEXT NOT NULL,
    activity_date TEXT NOT NULL,
    posts INTEGER NOT NULL DEFAULT 0,
    sales INTEGER NOT NULL DEFAULT 0,
    gmv_usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (creator_id, activity_date)
);

-- CreatorIQ doesn't expose a true per-post timestamp for this account (see
-- README "Adapting to your CreatorIQ account"), so "first post" is tracked
-- as a locally-observed proxy: the first date our own job sees a creator's
-- post count go above zero. Recorded once per creator so re-running (even
-- daily) never drifts the date forward.
CREATE TABLE IF NOT EXISTS first_post_observations (
    creator_id TEXT PRIMARY KEY,
    observed_at TEXT NOT NULL
);

-- Tracks creator lifecycle emails sent (see workflow/creator_emails.py):
-- one row per (creator_id, email_type), so a one-time email never resends,
-- and a repeating reminder knows when it last went out.
CREATE TABLE IF NOT EXISTS creator_emails (
    creator_id TEXT NOT NULL,
    email_type TEXT NOT NULL,
    last_sent_at TEXT NOT NULL,
    send_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (creator_id, email_type)
);
"""


class StateStore:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- creators -----------------------------------------------------

    def upsert_creators(self, creators: list[Creator]) -> None:
        rows = [(c.creator_id, c.name, c.email, c.joined_at.isoformat()) for c in creators]
        self._conn.executemany(
            "INSERT INTO creators (creator_id, name, email, joined_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(creator_id) DO UPDATE SET name=excluded.name, email=excluded.email, "
            "joined_at=excluded.joined_at",
            rows,
        )
        self._conn.commit()

    def get_creator(self, creator_id: str) -> Creator | None:
        row = self._conn.execute(
            "SELECT * FROM creators WHERE creator_id = ?", (creator_id,)
        ).fetchone()
        if row is None:
            return None
        return Creator.from_api(
            {
                "creator_id": row["creator_id"],
                "name": row["name"],
                "email": row["email"],
                "joined_at": row["joined_at"],
            }
        )

    def all_creators(self) -> list[Creator]:
        rows = self._conn.execute("SELECT * FROM creators").fetchall()
        return [
            Creator.from_api(
                {
                    "creator_id": r["creator_id"],
                    "name": r["name"],
                    "email": r["email"],
                    "joined_at": r["joined_at"],
                }
            )
            for r in rows
        ]

    # -- gift awards (idempotency) --------------------------------------

    def has_award(self, creator_id: str, milestone: Milestone) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM gift_awards WHERE creator_id = ? AND milestone = ?",
            (creator_id, milestone.value),
        ).fetchone()
        return row is not None

    def filter_unrecorded(self, awards: list[GiftAward]) -> list[GiftAward]:
        """Return only the awards that have not already been recorded (and thus not yet sheeted)."""

        return [a for a in awards if not self.has_award(a.creator.creator_id, a.milestone)]

    def record_awards(self, awards: list[GiftAward], recorded_at: datetime | None = None) -> None:
        recorded_at = recorded_at or datetime.now(timezone.utc)
        rows = [
            (
                a.creator.creator_id,
                a.milestone.value,
                a.amount_usd,
                a.completed_at.isoformat(),
                a.cohort_week_start.isoformat(),
                recorded_at.isoformat(),
            )
            for a in awards
        ]
        self._conn.executemany(
            "INSERT OR IGNORE INTO gift_awards "
            "(creator_id, milestone, amount_usd, completed_at, cohort_week_start, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def all_awards(self) -> list[GiftAward]:
        rows = self._conn.execute(
            "SELECT g.*, c.name, c.email, c.joined_at FROM gift_awards g "
            "JOIN creators c ON c.creator_id = g.creator_id"
        ).fetchall()
        awards = []
        for r in rows:
            creator = Creator.from_api(
                {
                    "creator_id": r["creator_id"],
                    "name": r["name"],
                    "email": r["email"],
                    "joined_at": r["joined_at"],
                }
            )
            awards.append(
                GiftAward(
                    creator=creator,
                    milestone=Milestone(r["milestone"]),
                    amount_usd=r["amount_usd"],
                    completed_at=datetime.fromisoformat(r["completed_at"]),
                    cohort_week_start=date.fromisoformat(r["cohort_week_start"]),
                )
            )
        return awards

    # -- first-post observation (proxy for a missing CreatorIQ timestamp) --

    def resolve_first_post_dates(
        self, post_counts: dict[str, int], today: date | None = None
    ) -> dict[str, date]:
        """Return each creator's "first post" date, persisting it the first time seen.

        `post_counts` is each creator's *current* cumulative post count (no
        date attached, straight from CreatorIQ). For any creator with a
        count > 0, this records today's date the first time it's seen and
        returns that same stored date on every later call -- so the
        "first post" date stays stable no matter how often this runs.
        Creators with a zero count are omitted from the result entirely.
        """

        today = today or date.today()
        result: dict[str, date] = {}
        for creator_id, count in post_counts.items():
            if count <= 0:
                continue
            row = self._conn.execute(
                "SELECT observed_at FROM first_post_observations WHERE creator_id = ?",
                (creator_id,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO first_post_observations (creator_id, observed_at) VALUES (?, ?)",
                    (creator_id, today.isoformat()),
                )
                result[creator_id] = today
            else:
                result[creator_id] = date.fromisoformat(row["observed_at"])
        self._conn.commit()
        return result

    # -- creator lifecycle emails -------------------------------------------

    def last_email_sent_at(self, creator_id: str, email_type: str) -> date | None:
        """The last date `email_type` was sent to this creator, or None if never."""

        row = self._conn.execute(
            "SELECT last_sent_at FROM creator_emails WHERE creator_id = ? AND email_type = ?",
            (creator_id, email_type),
        ).fetchone()
        return date.fromisoformat(row["last_sent_at"]) if row else None

    def record_email_sent(
        self, creator_id: str, email_type: str, sent_at: date | None = None
    ) -> None:
        sent_at = sent_at or date.today()
        self._conn.execute(
            "INSERT INTO creator_emails (creator_id, email_type, last_sent_at, send_count) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(creator_id, email_type) DO UPDATE SET "
            "last_sent_at = excluded.last_sent_at, send_count = send_count + 1",
            (creator_id, email_type, sent_at.isoformat()),
        )
        self._conn.commit()

    # -- activity (dashboard feed) ----------------------------------------

    def upsert_activity(self, records: list[ActivityRecord]) -> None:
        rows = [
            (r.creator_id, r.activity_date.isoformat(), r.posts, r.sales, r.gmv_usd)
            for r in records
        ]
        self._conn.executemany(
            "INSERT INTO activity (creator_id, activity_date, posts, sales, gmv_usd) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(creator_id, activity_date) DO UPDATE SET "
            "posts=excluded.posts, sales=excluded.sales, gmv_usd=excluded.gmv_usd",
            rows,
        )
        self._conn.commit()

    def get_activity(
        self,
        creator_ids: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[ActivityRecord]:
        query = "SELECT * FROM activity WHERE 1=1"
        params: list = []
        if creator_ids:
            placeholders = ",".join("?" for _ in creator_ids)
            query += f" AND creator_id IN ({placeholders})"
            params.extend(creator_ids)
        if start:
            query += " AND activity_date >= ?"
            params.append(start.isoformat())
        if end:
            query += " AND activity_date <= ?"
            params.append(end.isoformat())
        rows = self._conn.execute(query, params).fetchall()
        return [
            ActivityRecord(
                creator_id=r["creator_id"],
                activity_date=date.fromisoformat(r["activity_date"]),
                posts=r["posts"],
                sales=r["sales"],
                gmv_usd=r["gmv_usd"],
            )
            for r in rows
        ]
