"""Safety helpers for public LLM table talk."""

from __future__ import annotations

import re

from clue_core.constants import ROOMS, SUSPECTS, WEAPONS


_OWNERSHIP_VERBS = (" has ", " holds ", " owns ", " showed ", " must have ")


def sanitize_public_chat(text: str) -> str:
    candidate = str(text or "").strip()
    if not candidate:
        return ""
    normalized = f" {candidate.lower()} "
    mentions_card = any(name.lower() in normalized for name in [*SUSPECTS, *WEAPONS, *ROOMS])
    mentions_ownership = any(verb in normalized for verb in _OWNERSHIP_VERBS)
    if mentions_card and mentions_ownership:
        return ""
    return re.sub(r"\s+", " ", candidate)[:240]
