#!/usr/bin/env python3
"""CI helper: pull the latest `fast-track-db` GitHub Actions artifact into place.

Used by both scheduled workflows (see `.github/workflows/*.yml`) to bootstrap
local state before running -- replacing a more fragile `actions/cache`-based
approach. That approach had a real bug: `actions/cache` never overwrites a
cache entry once a given key exists, which silently breaks if a run is ever
re-run (the re-run's cache-save gets skipped because the *first* attempt
already claimed that key), so later runs kept restoring stale, pre-re-run
data indefinitely. Downloading the latest uploaded *artifact* instead --
the same mechanism `fast_track.dashboard.db_sync` already uses for the
dashboard -- has no such caveat: every job just always starts from
whatever the most recent successful run actually produced.

Exits 0 whether or not an artifact was found -- a first-ever run legitimately
has none yet, which isn't a failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fast_track.dashboard.db_sync import sync_db_from_github_artifact  # noqa: E402


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    dest_path = os.environ.get("FAST_TRACK_DB_PATH", "data/fast_track.db")

    if not repo or not token:
        print("GITHUB_REPOSITORY/GITHUB_TOKEN not set -- starting with an empty database.")
        return 0

    try:
        synced = sync_db_from_github_artifact(repo, token, dest_path)
    except Exception as exc:  # noqa: BLE001 -- never fail the job over a sync hiccup
        print(f"Could not sync from the latest artifact ({exc}); starting with an empty database.")
        return 0

    if synced:
        print(f"Synced {dest_path} from the latest fast-track-db artifact.")
    else:
        print("No prior fast-track-db artifact found -- starting with an empty database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
