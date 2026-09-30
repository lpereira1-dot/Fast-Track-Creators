import sys
from datetime import date
from pathlib import Path

from fast_track.storage.state_store import StateStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from db_email_log_baseline import main_guard, write_baseline  # noqa: E402


def test_guard_blocks_upload_when_email_log_shrinks(monkeypatch, tmp_path):
    db = tmp_path / "fast_track.db"
    monkeypatch.setenv("FAST_TRACK_DB_PATH", str(db))

    with StateStore(db) as store:
        store.record_email_sent("33830017", "welcome", sent_at=date(2026, 8, 17))
    write_baseline(db)

    with StateStore(db) as store:
        store._conn.execute("DELETE FROM creator_emails")
        store._conn.commit()

    assert main_guard() == 1
