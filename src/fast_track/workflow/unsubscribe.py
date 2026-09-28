"""Matching creators against lifecycle-email unsubscribe tokens.

Unsubscribe terms are stored in SQLite (see `StateStore`) and can be
supplemented via `CREATOR_EMAIL_UNSUBSCRIBES` for quick env-based blocks.
A term can be a CreatorIQ publisher id, display name, email, or a handle
like ``navoyhome`` that appears in the creator's id or name.
"""

from __future__ import annotations

from fast_track.models import Creator


def normalize_unsubscribe_term(term: str) -> str:
    # CreatorIQ ids are sometimes pasted with a trailing slash from URLs.
    return term.strip().strip("/").casefold()


def _compact_alphanumeric(value: str) -> str:
    """Lowercase letters/digits only — so ``navoyhome`` matches ``Navoy Home``."""

    return "".join(ch for ch in value.casefold() if ch.isalnum())


def creator_matches_unsubscribe(creator: Creator, term: str) -> bool:
    """Return True if this creator should be treated as unsubscribed for `term`."""

    token = normalize_unsubscribe_term(term)
    if not token:
        return False

    creator_id = creator.creator_id.casefold()
    name = creator.name.casefold()
    email = creator.email.casefold()
    local_part = email.split("@", 1)[0] if "@" in email else email

    if token in {creator_id, name, email, local_part}:
        return True

    compact_token = _compact_alphanumeric(term)
    if compact_token and compact_token in {
        _compact_alphanumeric(creator_id),
        _compact_alphanumeric(name),
        _compact_alphanumeric(local_part),
    }:
        return True

    # Handle-style identifiers (e.g. "navoyhome" in a publisher id or display name).
    return token in creator_id or token in name or (email and token in email)


def creator_is_unsubscribed(creator: Creator, terms: set[str]) -> bool:
    return any(creator_matches_unsubscribe(creator, term) for term in terms)
