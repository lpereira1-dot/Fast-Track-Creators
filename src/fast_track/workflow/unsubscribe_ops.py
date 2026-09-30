"""Helpers for recording lifecycle-email unsubscribes."""

from __future__ import annotations

from dataclasses import dataclass

from fast_track.models import Creator
from fast_track.storage.state_store import StateStore
from fast_track.workflow.unsubscribe import creator_matches_unsubscribe


@dataclass
class UnsubscribeResult:
    term: str
    matched_creators: list[Creator]
    note: str

    def summary(self) -> str:
        lines = [f"Recorded unsubscribe for '{self.term}'."]
        if self.matched_creators:
            lines.append(f"Matches {len(self.matched_creators)} creator(s) currently in the database:")
            for creator in self.matched_creators:
                label = creator.name or creator.creator_id
                email = creator.email or "(email not loaded yet)"
                lines.append(f"  - {label} <{email}> (id={creator.creator_id})")
        else:
            lines.append(
                "No creators in the local database matched yet — the term is still saved "
                "and will block anyone who matches on future runs (e.g. after the weekly sync)."
            )
        return "\n".join(lines)


def record_unsubscribe(store: StateStore, term: str, note: str = "") -> UnsubscribeResult:
    store.add_email_unsubscribe(term, note=note)
    matched = [c for c in store.all_creators() if creator_matches_unsubscribe(c, term)]
    return UnsubscribeResult(term=term.strip(), matched_creators=matched, note=note)
