import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from fast_track.dashboard.db_sync import sync_db_from_github_artifact

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_db_from_artifact  # noqa: E402


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)
    return buffer.getvalue()


def _fake_response(json_data=None, content=b"", status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_data or {}
    response.content = content
    response.raise_for_status = Mock()
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"status {status_code}")
    return response


def test_sync_downloads_and_extracts_matching_db_file(tmp_path):
    zip_content = _zip_bytes({"data/fast_track.db": b"sqlite-bytes-here"})
    list_response = _fake_response(
        json_data={"artifacts": [{"archive_download_url": "https://example.com/artifact.zip"}]}
    )
    zip_response = _fake_response(content=zip_content)

    with patch("requests.get", side_effect=[list_response, zip_response]) as mock_get:
        dest = tmp_path / "out" / "fast_track.db"
        result = sync_db_from_github_artifact("owner/repo", "tok", dest)

    assert result is True
    assert dest.read_bytes() == b"sqlite-bytes-here"
    list_call = mock_get.call_args_list[0]
    assert list_call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert list_call.kwargs["params"]["name"] == "fast-track-db"


def test_sync_returns_false_when_no_artifacts_found(tmp_path):
    list_response = _fake_response(json_data={"artifacts": []})

    with patch("requests.get", return_value=list_response):
        result = sync_db_from_github_artifact("owner/repo", "tok", tmp_path / "fast_track.db")

    assert result is False
    assert not (tmp_path / "fast_track.db").exists()


def test_sync_returns_false_when_zip_has_no_db_file(tmp_path):
    zip_content = _zip_bytes({"README.txt": b"nothing useful here"})
    list_response = _fake_response(
        json_data={"artifacts": [{"archive_download_url": "https://example.com/artifact.zip"}]}
    )
    zip_response = _fake_response(content=zip_content)

    with patch("requests.get", side_effect=[list_response, zip_response]):
        result = sync_db_from_github_artifact("owner/repo", "tok", tmp_path / "fast_track.db")

    assert result is False


def test_sync_raises_on_http_error(tmp_path):
    error_response = _fake_response(status_code=401)

    with patch("requests.get", return_value=error_response), pytest.raises(requests.HTTPError):
        sync_db_from_github_artifact("owner/repo", "tok", tmp_path / "fast_track.db")


class TestCiSyncScript:
    """Covers scripts/sync_db_from_artifact.py -- the CI-bootstrap entrypoint

    that replaced actions/cache in the scheduled workflows (see its
    docstring for why). Never raises/exits non-zero: a sync problem here
    should degrade to "start with an empty database", not fail the job.
    """

    def test_exits_cleanly_when_env_vars_missing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("FAST_TRACK_DB_PATH", str(tmp_path / "fast_track.db"))

        assert sync_db_from_artifact.main() == 0

    def test_exits_cleanly_and_syncs_when_artifact_found(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        dest = tmp_path / "fast_track.db"
        monkeypatch.setenv("FAST_TRACK_DB_PATH", str(dest))

        with patch.object(sync_db_from_artifact, "sync_db_from_github_artifact", return_value=True):
            assert sync_db_from_artifact.main() == 0

    def test_exits_cleanly_when_underlying_sync_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("FAST_TRACK_DB_PATH", str(tmp_path / "fast_track.db"))

        with patch.object(
            sync_db_from_artifact,
            "sync_db_from_github_artifact",
            side_effect=requests.HTTPError("boom"),
        ):
            assert sync_db_from_artifact.main() == 0
