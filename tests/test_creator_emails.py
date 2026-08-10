from datetime import date

from fast_track.config import Settings
from fast_track.models import ActivationRecord, Creator
from fast_track.storage.state_store import StateStore
from fast_track.workflow.creator_emails import (
    POST_REMINDER,
    SALE_CONGRATS,
    SALE_REMINDER,
    WELCOME,
    run_creator_email_job,
)


def _creator(cid: str, joined: str) -> Creator:
    return Creator.from_api(
        {"creator_id": cid, "name": cid, "email": f"{cid}@example.com", "joined_at": joined}
    )


class FakeActivationClient:
    """Fake ReportsClient exposing only fetch_activation (all this job needs)."""

    def __init__(self, activations: dict[str, ActivationRecord]):
        self._activations = activations

    def fetch_activation(self, creator_ids):
        return [self._activations[cid] for cid in creator_ids if cid in self._activations]


class FakeEmailSender:
    def __init__(self, always_fail: bool = False):
        self.sent: list[tuple[str, str, list[str]]] = []
        self.always_fail = always_fail

    def send_bulk_email(self, subject, html_body, creator_ids):
        if self.always_fail:
            raise RuntimeError("simulated send failure")
        self.sent.append((subject, html_body, list(creator_ids)))


def _settings() -> Settings:
    return Settings()


def test_day_zero_creator_gets_welcome_only(tmp_path):
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        client = FakeActivationClient({})
        sender = FakeEmailSender()
        result = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 1))

    assert [(c.creator_id, t) for c, t in result.sent] == [("c-1", WELCOME)]
    assert len(sender.sent) == 1
    assert sender.sent[0][2] == ["c-1"]


def test_no_post_before_day_seven_gets_no_reminder(tmp_path):
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        store.record_email_sent("c-1", WELCOME, sent_at=date(2026, 8, 1))
        client = FakeActivationClient({})
        sender = FakeEmailSender()
        result = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 5))

    assert result.sent == []


def test_no_post_on_day_seven_gets_post_reminder(tmp_path):
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        store.record_email_sent("c-1", WELCOME, sent_at=date(2026, 8, 1))
        client = FakeActivationClient({})
        sender = FakeEmailSender()
        result = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 8))

    assert [(c.creator_id, t) for c, t in result.sent] == [("c-1", POST_REMINDER)]


def test_post_reminder_repeats_after_interval_but_not_before(tmp_path):
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        store.record_email_sent("c-1", WELCOME, sent_at=date(2026, 8, 1))
        store.record_email_sent("c-1", POST_REMINDER, sent_at=date(2026, 8, 8))
        client = FakeActivationClient({})
        sender = FakeEmailSender()

        result_early = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 9))
        assert result_early.sent == []

        result_due = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 10))
        assert [(c.creator_id, t) for c, t in result_due.sent] == [("c-1", POST_REMINDER)]


def test_posted_creator_gets_sale_reminder_not_post_reminder(tmp_path):
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    activation = ActivationRecord.from_api(
        {"creator_id": "c-1", "first_post_at": "2026-08-02T00:00:00Z", "first_sale_at": None}
    )
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        store.record_email_sent("c-1", WELCOME, sent_at=date(2026, 8, 1))
        client = FakeActivationClient({"c-1": activation})
        sender = FakeEmailSender()
        result = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 8))

    assert [(c.creator_id, t) for c, t in result.sent] == [("c-1", SALE_REMINDER)]


def test_sale_reminder_repeats_after_interval_but_not_before(tmp_path):
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    activation = ActivationRecord.from_api(
        {"creator_id": "c-1", "first_post_at": "2026-08-02T00:00:00Z", "first_sale_at": None}
    )
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        store.record_email_sent("c-1", WELCOME, sent_at=date(2026, 8, 1))
        store.record_email_sent("c-1", SALE_REMINDER, sent_at=date(2026, 8, 3))
        client = FakeActivationClient({"c-1": activation})
        sender = FakeEmailSender()

        result_early = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 4))
        assert result_early.sent == []

        result_due = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 5))
        assert [(c.creator_id, t) for c, t in result_due.sent] == [("c-1", SALE_REMINDER)]


def test_qualifying_sale_triggers_congrats_and_skips_other_reminders(tmp_path):
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    activation = ActivationRecord.from_api(
        {
            "creator_id": "c-1",
            "first_post_at": "2026-08-02T00:00:00Z",
            "first_sale_at": "2026-08-03T00:00:00Z",
        }
    )
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        store.record_email_sent("c-1", WELCOME, sent_at=date(2026, 8, 1))
        client = FakeActivationClient({"c-1": activation})
        sender = FakeEmailSender()
        result = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 4))

    assert [(c.creator_id, t) for c, t in result.sent] == [("c-1", SALE_CONGRATS)]


def test_sale_congrats_never_resends(tmp_path):
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    activation = ActivationRecord.from_api(
        {
            "creator_id": "c-1",
            "first_post_at": "2026-08-02T00:00:00Z",
            "first_sale_at": "2026-08-03T00:00:00Z",
        }
    )
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        store.record_email_sent("c-1", WELCOME, sent_at=date(2026, 8, 1))
        store.record_email_sent("c-1", SALE_CONGRATS, sent_at=date(2026, 8, 4))
        client = FakeActivationClient({"c-1": activation})
        sender = FakeEmailSender()
        result = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 5))

    assert result.sent == []


def test_sale_after_activation_window_does_not_trigger_congrats(tmp_path):
    creator = _creator("c-1", "2026-07-01T00:00:00Z")
    activation = ActivationRecord.from_api(
        {
            "creator_id": "c-1",
            "first_post_at": "2026-07-02T00:00:00Z",
            "first_sale_at": "2026-07-20T00:00:00Z",  # day 19 -- outside the 14-day window
        }
    )
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        client = FakeActivationClient({"c-1": activation})
        sender = FakeEmailSender()
        result = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 7, 20))

    assert result.sent == []


def test_no_emails_after_day_fourteen(tmp_path):
    creator = _creator("c-1", "2026-07-01T00:00:00Z")
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        client = FakeActivationClient({})
        sender = FakeEmailSender()
        result = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 7, 20))

    assert result.sent == []


def test_dry_run_does_not_send_or_record(tmp_path):
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        client = FakeActivationClient({})
        sender = FakeEmailSender()
        result = run_creator_email_job(
            client, sender, store, _settings(), today=date(2026, 8, 1), dry_run=True
        )

        assert [(c.creator_id, t) for c, t in result.sent] == [("c-1", WELCOME)]
        assert sender.sent == []
        assert store.last_email_sent_at("c-1", WELCOME) is None


def test_multiple_creators_due_for_same_email_are_batched_into_one_send(tmp_path):
    c1 = _creator("c-1", "2026-08-01T00:00:00Z")
    c2 = _creator("c-2", "2026-08-01T00:00:00Z")
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([c1, c2])
        client = FakeActivationClient({})
        sender = FakeEmailSender()
        result = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 1))

    assert len(sender.sent) == 1
    assert sorted(sender.sent[0][2]) == ["c-1", "c-2"]
    assert len(result.sent) == 2


def test_send_failure_does_not_record_and_is_retried_next_run(tmp_path):
    creator = _creator("c-1", "2026-08-01T00:00:00Z")
    with StateStore(tmp_path / "state.db") as store:
        store.upsert_creators([creator])
        client = FakeActivationClient({})
        sender = FakeEmailSender(always_fail=True)
        result = run_creator_email_job(client, sender, store, _settings(), today=date(2026, 8, 1))

        assert result.sent == []
        assert store.last_email_sent_at("c-1", WELCOME) is None
