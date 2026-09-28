from datetime import date

from fast_track.models import Creator
from fast_track.storage.state_store import StateStore
from fast_track.workflow.creator_emails import run_creator_email_job
from fast_track.workflow.unsubscribe import creator_matches_unsubscribe
from fast_track.workflow.unsubscribe_ops import record_unsubscribe
from tests.test_creator_emails import FakeActivationClient, FakeEmailSender, _creator, _settings


def test_creator_matches_handle_in_name():
    creator = Creator.from_api(
        {
            "creator_id": "pub-999",
            "name": "Navoy Home",
            "email": "someone@example.com",
            "joined_at": "2026-08-01T00:00:00Z",
        }
    )
    assert creator_matches_unsubscribe(creator, "navoyhome")


def test_creator_matches_publisher_id():
    creator = Creator.from_api(
        {
            "creator_id": "navoyhome",
            "name": "Creator",
            "email": "",
            "joined_at": "2026-08-01T00:00:00Z",
        }
    )
    assert creator_matches_unsubscribe(creator, "navoyhome")


def test_record_unsubscribe_blocks_lifecycle_email(tmp_path):
    creator = _creator("navoyhome", "2026-08-01T00:00:00Z")
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        record_unsubscribe(store, "navoyhome")
        sender = FakeEmailSender()
        result = run_creator_email_job(
            FakeActivationClient({}),
            sender,
            store,
            _settings(),
            today=date(2026, 8, 1),
        )
    assert result.sent == []
    assert sender.sent == []


def test_env_unsubscribe_blocks_without_db_row(tmp_path, monkeypatch):
    monkeypatch.setenv("CREATOR_EMAIL_UNSUBSCRIBES", "navoyhome")
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    creator = Creator.from_api(
        {
            "creator_id": "c-1",
            "name": "navoyhome",
            "email": "c-1@example.com",
            "joined_at": "2026-08-01T00:00:00Z",
        }
    )
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        sender = FakeEmailSender()
        result = run_creator_email_job(
            FakeActivationClient({}),
            sender,
            store,
            _settings(),
            today=date(2026, 8, 1),
        )
    assert result.sent == []
    assert sender.sent == []
