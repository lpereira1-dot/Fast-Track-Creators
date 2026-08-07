"""Gift-card eligibility rules for the creator activation test.

Rules under test:
  - $25 gift card for a creator's FIRST POST within `activation_window_days`
    (default 14) days of joining the program.
  - An additional $25 gift card for a creator's FIRST SALE within the same
    window.
"""

from __future__ import annotations

from fast_track.config import ProgramRules
from fast_track.models import ActivationRecord, Creator, GiftAward, Milestone

_MILESTONE_CONFIG = (Milestone.FIRST_POST, Milestone.FIRST_SALE)


def evaluate_awards(
    creator: Creator, activation: ActivationRecord, rules: ProgramRules
) -> list[GiftAward]:
    """Return every milestone (0, 1, or 2) this creator has qualified for so far."""

    amounts = {
        Milestone.FIRST_POST: rules.first_post_gift_usd,
        Milestone.FIRST_SALE: rules.first_sale_gift_usd,
    }
    awards: list[GiftAward] = []
    for milestone in _MILESTONE_CONFIG:
        completed_at = activation.completed_at(milestone)
        if completed_at is None:
            continue
        days_to_complete = (completed_at.date() - creator.joined_at.date()).days
        if 0 <= days_to_complete <= rules.activation_window_days:
            awards.append(
                GiftAward(
                    creator=creator,
                    milestone=milestone,
                    amount_usd=amounts[milestone],
                    completed_at=completed_at,
                    cohort_week_start=creator.cohort_week_start(rules.cohort_week_start_weekday),
                )
            )
    return awards


def evaluate_all_awards(
    creators: list[Creator],
    activations: dict[str, ActivationRecord],
    rules: ProgramRules,
) -> list[GiftAward]:
    """Evaluate awards for many creators at once, keyed by creator_id -> ActivationRecord."""

    all_awards: list[GiftAward] = []
    for creator in creators:
        activation = activations.get(creator.creator_id)
        if activation is None:
            continue
        all_awards.extend(evaluate_awards(creator, activation, rules))
    return all_awards
