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
    intent: str = ""
    target_seat_id: str = ""
    topic: str = ""
    tone: str = ""
    thread_action: str = ""
    relationship_deltas: list[dict[str, Any]] = field(default_factory=list)
    action_pressure_hint: str = ""
    thread_id: str = ""
    rationale_private: str = ""
    debug_private: dict[str, Any] = field(default_factory=dict)
    agent_meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatDecision":
        """Build one chat decision from a structured JSON-like payload."""

        return cls(
            speak=bool(payload.get("speak")),
            text=str(payload.get("text", "") or ""),
            intent=str(payload.get("intent", "") or ""),
            target_seat_id=str(payload.get("target_seat_id", "") or ""),
            topic=str(payload.get("topic", "") or ""),
            tone=str(payload.get("tone", "") or ""),
            thread_action=str(payload.get("thread_action", "") or ""),
            relationship_deltas=[dict(item) for item in list(payload.get("relationship_deltas") or []) if isinstance(item, dict)],
            action_pressure_hint=str(payload.get("action_pressure_hint", "") or ""),
            thread_id=str(payload.get("thread_id", "") or ""),
            rationale_private=str(payload.get("rationale_private", "") or ""),
            debug_private=dict(payload.get("debug_private") or {}),
            agent_meta=dict(payload.get("agent_meta") or {}),
        )


@dataclass(slots=True)
class MemorySummaryDecision:
    """Normalized durable memory summary returned by an LLM seat agent."""

    summary: dict[str, Any]
    rationale_private: str = ""
    relationship_updates: list[dict[str, Any]] = field(default_factory=list)
    debug_private: dict[str, Any] = field(default_factory=dict)
    agent_meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MemorySummaryDecision":
        """Build one durable memory decision from structured model output."""

        relationship_updates = [
            dict(item)
            for item in list(payload.get("relationship_updates") or [])
            if isinstance(item, dict)
        ]
        summary = {
            "first_person_summary": str(payload.get("first_person_summary", "") or ""),
            "strategic_lessons": [str(item) for item in list(payload.get("strategic_lessons") or []) if str(item).strip()],
            "social_observations": [str(item) for item in list(payload.get("social_observations") or []) if str(item).strip()],
            "grudges": [str(item) for item in list(payload.get("grudges") or []) if str(item).strip()],
            "favors": [str(item) for item in list(payload.get("favors") or []) if str(item).strip()],
            "future_play_cues": [str(item) for item in list(payload.get("future_play_cues") or []) if str(item).strip()],
            "relationship_updates": relationship_updates,
        }
        return cls(
            summary=summary,
            relationship_updates=relationship_updates,
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
