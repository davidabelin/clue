"""Shared interfaces for seat agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
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
    debug_private: dict[str, Any] = field(default_factory=dict)
    agent_meta: dict[str, Any] = field(default_factory=dict)

    def to_action_payload(self) -> dict[str, Any]:
        """Drop purely explanatory fields before handing the action to the rules engine."""

        payload = {key: value for key, value in asdict(self).items() if value is not None}
        payload.pop("rationale_private", None)
        payload.pop("debug_private", None)
        payload.pop("agent_meta", None)
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
            debug_private=dict(payload.get("debug_private") or {}),
            agent_meta=dict(payload.get("agent_meta") or {}),
        )


@dataclass(slots=True)
class ChatDecision:
    """Normalized public-chat payload returned by autonomous seat agents."""

    speak: bool
    text: str = ""
    rationale_private: str = ""
    debug_private: dict[str, Any] = field(default_factory=dict)
    agent_meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatDecision":
        """Build one chat decision from a structured JSON-like payload."""

        return cls(
            speak=bool(payload.get("speak")),
            text=str(payload.get("text", "") or ""),
            rationale_private=str(payload.get("rationale_private", "") or ""),
            debug_private=dict(payload.get("debug_private") or {}),
            agent_meta=dict(payload.get("agent_meta") or {}),
        )


class SeatAgent(ABC):
    """Common interface for any non-human Clue seat policy."""

    @abstractmethod
    def decide_turn(self, *, snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> TurnDecision:
        """Choose one legal next action from the current seat-local snapshot."""

        raise NotImplementedError

    @abstractmethod
    def decide_chat(self, *, snapshot: dict[str, Any]) -> ChatDecision:
        """Choose whether to post one public chat line from the current seat-local snapshot."""

        raise NotImplementedError
