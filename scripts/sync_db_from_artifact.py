#!/usr/bin/env python3
"""CI helper: pull the latest `fast-track-db` GitHub Actions artifact into place.

Used by scheduled workflows (see `.github/workflows/*.yml`) to bootstrap
local state before running -- replacing a more fragile `actions/cache`-based
approach.

Exit codes (when `FAST_TRACK_STRICT_DB_SYNC=1`, the default in workflows):
  0 -- success (restored an artifact, or no prior artifact yet)
  2 -- could not restore and strict mode is on (job should abort so we
       never upload a blank DB over good send-history)

With strict mode off, sync errors degrade to "start fresh" for local runs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fast_track.dashboard.db_sync import sync_db_from_github_artifact  # noqa: E402

# Imported after sys.path tweak; sibling script, not installed as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from db_email_log_baseline import write_baseline  # noqa: E402


def _strict() -> bool:
    return os.environ.get("FAST_TRACK_STRICT_DB_SYNC", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    dest_path = os.environ.get("FAST_TRACK_DB_PATH", "data/fast_track.db")

    if not repo or not token:
        message = "GITHUB_REPOSITORY/GITHUB_TOKEN not set."
        if _strict():
            print(f"{message} Aborting (strict DB sync).")
            return 2
        print(f"{message} Starting with an empty database.")
        return 0

    try:
        synced = sync_db_from_github_artifact(repo, token, dest_path)
    except Exception as exc:  # noqa: BLE001
        if _strict():
            print(f"Could not sync from the latest artifact ({exc}). Aborting (strict DB sync).")
            return 2
        print(f"Could not sync from the latest artifact ({exc}); starting with an empty database.")
        return 0

    if synced:
        print(f"Synced {dest_path} from the latest fast-track-db artifact.")
    else:
        print("No prior fast-track-db artifact found -- starting with an empty database.")

    write_baseline(Path(dest_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
