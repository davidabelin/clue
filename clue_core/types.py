"""Shared dataclasses and typed helpers for the Clue app."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from clue_core.constants import CHARACTERS


@dataclass(slots=True)
class SeatConfig:
    """Normalized seat configuration used by setup, storage, and web payloads."""

    seat_id: str
    display_name: str
    character: str
    seat_kind: str = "human"
    agent_model: str = ""
    agent_profile: str = ""
    notebook: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate character identity and normalize the seat kind."""

        if self.character not in CHARACTERS:
            raise ValueError(f"Unsupported character: {self.character}")
        normalized = str(self.seat_kind or "human").strip().lower()
        if normalized not in {"human", "heuristic", "llm"}:
            raise ValueError("seat_kind must be one of: human, heuristic, llm.")
        self.seat_kind = normalized

    def to_dict(self) -> dict[str, Any]:
        """Serialize the config into the dict shape used across the app."""

        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SeatConfig":
        """Build one config from a request payload or persisted row."""

        return cls(
            seat_id=str(payload["seat_id"]),
            display_name=str(payload["display_name"]),
            character=str(payload["character"]),
            seat_kind=str(payload.get("seat_kind", "human")),
            agent_model=str(payload.get("agent_model", "")),
            agent_profile=str(payload.get("agent_profile", "")),
            notebook=dict(payload.get("notebook") or {}),
        )
