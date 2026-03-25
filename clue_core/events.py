"""Event helpers for append-only gameplay history."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_event(
    event_type: str,
    *,
    payload: dict[str, Any] | None = None,
    message: str,
    visibility: str = "public",
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "payload": payload or {},
        "message": message,
        "visibility": visibility,
        "created_at": utcnow_iso(),
    }
