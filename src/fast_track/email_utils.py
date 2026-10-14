"""Shared helpers for creator contact emails."""

from __future__ import annotations

import re

# Practical check for sheet ordering and CRM sends — not full RFC 5322.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def is_valid_email(value: str | None) -> bool:
    if not value:
        return False
    candidate = value.strip()
    if not candidate or "@" not in candidate:
        return False
    return bool(_EMAIL_RE.match(candidate))


def normalize_email(value: str | None) -> str:
    if not value:
        return ""
    return value.strip()
