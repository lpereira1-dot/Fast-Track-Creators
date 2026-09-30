"""Read/write the lifecycle-email log row count beside the SQLite DB.

Scheduled workflows snapshot the count immediately after restoring
`fast-track.db` from the latest artifact. Before uploading a new artifact,
`guard_db_upload.py` ensures that count never *decreased* -- catching the
failure mode where a job started from an empty DB (e.g. artifact download
failed) and would otherwise overwrite good send-history with a blank file,
which makes welcome emails go out again every day.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fast_track.storage.state_store import StateStore  # noqa: E402


def db_path() -> Path:
    return Path(os.environ.get("FAST_TRACK_DB_PATH", "data/fast_track.db"))


def baseline_path(db: Path) -> Path:
    return db.with_name(db.name + ".email_log_baseline")


def count_lifecycle_email_rows(db: Path) -> int:
    if not db.exists():
        return 0
    with StateStore(db) as store:
        return store.lifecycle_email_log_count()


def write_baseline(db: Path | None = None) -> int:
    db = db or db_path()
    count = count_lifecycle_email_rows(db)
    baseline_path(db).write_text(str(count))
    return count


def read_baseline(db: Path | None = None) -> int | None:
    db = db or db_path()
    path = baseline_path(db)
    if not path.exists():
        return None
    return int(path.read_text().strip())


def main_write() -> int:
    count = write_baseline()
    print(f"Wrote lifecycle email log baseline: {count} row(s).")
    return 0


def main_guard() -> int:
    db = db_path()
    baseline = read_baseline(db)
    current = count_lifecycle_email_rows(db)
    if baseline is None:
        print("No email-log baseline file -- skipping upload guard.")
        return 0
    if current < baseline:
        print(
            f"Refusing to upload database: lifecycle email log shrank from {baseline} "
            f"to {current} row(s). This usually means the job started from an empty "
            "database after a failed artifact restore and would erase send history "
            "(causing welcome emails to repeat daily). Re-run after fixing artifact sync."
        )
        return 1
    print(f"Upload guard OK ({current} lifecycle email log row(s), baseline {baseline}).")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "write"
    if cmd == "guard":
        raise SystemExit(main_guard())
    raise SystemExit(main_write())
