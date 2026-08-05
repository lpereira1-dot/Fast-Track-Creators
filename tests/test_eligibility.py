
from fast_track.config import ProgramRules
from fast_track.models import ActivationRecord, Creator, Milestone
from fast_track.workflow.eligibility import evaluate_all_awards, evaluate_awards

RULES = ProgramRules(
    activation_window_days=14,
    first_post_gift_usd=25.0,
    first_sale_gift_usd=25.0,
    retention_window_days=30,
    cohort_week_start_weekday=1,
)


def creator(joined: str) -> Creator:
    return Creator.from_api(
        {"creator_id": "c-1", "name": "Ava", "email": "ava@example.com", "joined_at": joined}
    )


def test_first_post_within_window_qualifies():
    c = creator("2026-06-01T00:00:00Z")
    activation = ActivationRecord.from_api(
        {"creator_id": "c-1", "first_post_at": "2026-06-10T00:00:00Z", "first_sale_at": None}
    )
    awards = evaluate_awards(c, activation, RULES)
    assert len(awards) == 1
    assert awards[0].milestone is Milestone.FIRST_POST
    assert awards[0].amount_usd == 25.0


def test_post_exactly_on_boundary_day_qualifies():
    c = creator("2026-06-01T00:00:00Z")
    activation = ActivationRecord.from_api(
        {"creator_id": "c-1", "first_post_at": "2026-06-15T00:00:00Z", "first_sale_at": None}
    )
    awards = evaluate_awards(c, activation, RULES)
    assert len(awards) == 1


def test_post_one_day_past_boundary_does_not_qualify():
    c = creator("2026-06-01T00:00:00Z")
    activation = ActivationRecord.from_api(
        {"creator_id": "c-1", "first_post_at": "2026-06-16T00:00:00Z", "first_sale_at": None}
    )
    awards = evaluate_awards(c, activation, RULES)
    assert awards == []


def test_both_milestones_within_window_earn_both_gifts():
    c = creator("2026-06-01T00:00:00Z")
    activation = ActivationRecord.from_api(
        {
            "creator_id": "c-1",
            "first_post_at": "2026-06-02T00:00:00Z",
            "first_sale_at": "2026-06-14T00:00:00Z",
        }
    )
    awards = evaluate_awards(c, activation, RULES)
    milestones = {a.milestone for a in awards}
    assert milestones == {Milestone.FIRST_POST, Milestone.FIRST_SALE}
    assert sum(a.amount_usd for a in awards) == 50.0


def test_no_activity_earns_nothing():
    c = creator("2026-06-01T00:00:00Z")
    activation = ActivationRecord.from_api(
        {"creator_id": "c-1", "first_post_at": None, "first_sale_at": None}
    )
    assert evaluate_awards(c, activation, RULES) == []


def test_sale_before_join_date_is_ignored_defensively():
    # Shouldn't happen in practice, but guards against bad/backfilled data.
    c = creator("2026-06-10T00:00:00Z")
    activation = ActivationRecord.from_api(
        {"creator_id": "c-1", "first_post_at": None, "first_sale_at": "2026-06-01T00:00:00Z"}
    )
    assert evaluate_awards(c, activation, RULES) == []


def test_evaluate_all_awards_skips_creators_without_activation_data():
    c1 = Creator.from_api(
        {"creator_id": "c-1", "name": "A", "email": "a@example.com", "joined_at": "2026-06-01T00:00:00Z"}
    )
    c2 = Creator.from_api(
        {"creator_id": "c-2", "name": "B", "email": "b@example.com", "joined_at": "2026-06-01T00:00:00Z"}
    )
    activations = {
        "c-1": ActivationRecord.from_api(
            {"creator_id": "c-1", "first_post_at": "2026-06-02T00:00:00Z", "first_sale_at": None}
        )
    }
    awards = evaluate_all_awards([c1, c2], activations, RULES)
    assert len(awards) == 1
    assert awards[0].creator.creator_id == "c-1"
