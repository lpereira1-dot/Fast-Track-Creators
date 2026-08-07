"""Weekly cohort bucketing for newly-joined creators."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from fast_track.config import ProgramRules
from fast_track.models import Creator


def group_into_weekly_cohorts(
    creators: list[Creator], rules: ProgramRules
) -> dict[date, list[Creator]]:
    """Bucket creators by the Monday (configurable) of the week they joined."""

    cohorts: dict[date, list[Creator]] = defaultdict(list)
    for creator in creators:
        week_start = creator.cohort_week_start(rules.cohort_week_start_weekday)
        cohorts[week_start].append(creator)
    return dict(sorted(cohorts.items()))
