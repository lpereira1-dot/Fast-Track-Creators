"""Pulls the latest `data/fast_track.db` snapshot from GitHub Actions.

The scheduled weekly-cohort/daily-activity-sync jobs run in GitHub Actions
and persist their SQLite state as a `fast-track-db` artifact (see
`.github/workflows/*.yml`) -- but a dashboard hosted elsewhere (e.g.
Streamlit Community Cloud) is a completely separate runtime with no access
to that filesystem. This downloads the latest such artifact so a
separately-hosted dashboard can reflect the same data the scheduled jobs
produced, without standing up a shared database service.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import requests

DEFAULT_ARTIFACT_NAME = "fast-track-db"


def sync_db_from_github_artifact(
    repo: str,
    token: str,
    dest_path: str | Path,
    artifact_name: str = DEFAULT_ARTIFACT_NAME,
    timeout_seconds: int = 30,
) -> bool:
    """Download the most recent `artifact_name` artifact for `repo` (e.g.
    `"owner/repo"`) and extract its `fast_track.db` file to `dest_path`.

    Returns True if a file was written, False if no matching artifact
    exists yet (e.g. the scheduled jobs haven't run since this artifact
    name was introduced). Raises `requests.RequestException` on
    network/auth errors -- the caller decides how to surface those (see
    `dashboard/app.py`, which shows a non-fatal warning rather than
    crashing the page).
    """

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    list_url = f"https://api.github.com/repos/{repo}/actions/artifacts"
    response = requests.get(
        list_url,
        headers=headers,
        params={"name": artifact_name, "per_page": 1},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    artifacts = response.json().get("artifacts", [])
    if not artifacts:
        return False

    download_url = artifacts[0]["archive_download_url"]
    zip_response = requests.get(download_url, headers=headers, timeout=timeout_seconds)
    zip_response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        db_entry = next((n for n in archive.namelist() if Path(n).name == "fast_track.db"), None)
        if db_entry is None:
            return False
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive.read(db_entry))
    return True
