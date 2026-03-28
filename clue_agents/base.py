"""Shared interfaces for seat agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class TurnDecision:
    """Normalized action payload returned by heuristic or LLM seat agents."""

    action: str
    target_node: str | None = None
    suspect: str | None = None
    weapon: str | None = None
    room: str | None = None
    card: str | None = None
    text: str | None = None
    rationale_private: str = ""

    def to_action_payload(self) -> dict[str, Any]:
        """Drop purely explanatory fields before handing the action to the rules engine."""

        payload = {key: value for key, value in asdict(self).items() if value is not None}
        payload.pop("rationale_private", None)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TurnDecision":
        """Build one decision from a structured JSON-like payload."""

        return cls(
            action=str(payload.get("action", "")),
            target_node=payload.get("target_node"),
            suspect=payload.get("suspect"),
            weapon=payload.get("weapon"),
            room=payload.get("room"),
            card=payload.get("card"),
            text=payload.get("text"),
            rationale_private=str(payload.get("rationale_private", "")),
        )


class SeatAgent(ABC):
    """Common interface for any non-human Clue seat policy."""

    @abstractmethod
    def decide_turn(self, *, snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> TurnDecision:
        """Choose one legal next action from the current seat-local snapshot."""

        raise NotImplementedError
