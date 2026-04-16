"""Shared dataclasses and typed helpers for the Clue app."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from clue_core.constants import CHARACTERS


DEFAULT_UI_MODE = "beginner"
LIVE_UI_MODES = {"beginner", "player"}
UNAVAILABLE_UI_MODES = {"superplayer"}


def normalize_ui_mode(value: Any, *, default: str = DEFAULT_UI_MODE) -> str:
    """Return a normalized live UI mode, falling back to Beginner when absent."""

    return str(value or default).strip().lower()


@dataclass(slots=True)
class SeatConfig:
    """Normalized seat configuration used by setup, storage, and web payloads."""

    seat_id: str
    display_name: str
    character: str
    seat_kind: str = "human"
    agent_model: str = ""
    agent_profile: str = ""
    agent_chat_model: str = ""
    agent_chat_profile: str = ""
    ui_mode: str = DEFAULT_UI_MODE
    notebook: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate character identity and normalize the seat kind."""

        if self.character not in CHARACTERS:
            raise ValueError(f"Unsupported character: {self.character}")
        normalized = str(self.seat_kind or "human").strip().lower()
        if normalized not in {"human", "heuristic", "llm"}:
            raise ValueError("seat_kind must be one of: human, heuristic, llm.")
        self.seat_kind = normalized
        ui_mode = normalize_ui_mode(self.ui_mode)
        if ui_mode not in LIVE_UI_MODES:
            allowed = ", ".join(sorted(LIVE_UI_MODES))
            raise ValueError(f"ui_mode must be one of: {allowed}.")
        self.ui_mode = ui_mode

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
            agent_chat_model=str(payload.get("agent_chat_model", "")),
            agent_chat_profile=str(payload.get("agent_chat_profile", "")),
            ui_mode=str(payload.get("ui_mode", DEFAULT_UI_MODE)),
            notebook=dict(payload.get("notebook") or {}),
        )
