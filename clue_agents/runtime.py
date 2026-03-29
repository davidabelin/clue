"""Seat-agent runtime selection, configuration, and cleanup helpers."""

from __future__ import annotations

from typing import Any

from clue_agents.base import SeatAgent, TurnDecision
from clue_agents.config import load_llm_runtime_config
from clue_agents.heuristic import HeuristicSeatAgent
from clue_agents.llm import LLMSeatAgent
from clue_agents.sdk_runtime import AGENTS_SDK_AVAILABLE


class AgentRuntime:
    """Instantiate the correct autonomous seat policy for persisted seat rows.

    The runtime owns shared, process-wide configuration for autonomous seats but
    does not hold game state. Each turn still flows through the deterministic
    repository and ``GameMaster`` pipeline.
    """

    def __init__(self) -> None:
        """Cache the shared heuristic agent and the normalized LLM runtime config."""

        self._heuristic = HeuristicSeatAgent()
        self._llm_runtime_config = load_llm_runtime_config()

    def agent_for_seat(self, seat: dict[str, Any]) -> SeatAgent:
        """Resolve one autonomous agent implementation from the stored seat kind."""

        seat_kind = str(seat.get("seat_kind", "human")).strip().lower()
        if seat_kind == "heuristic":
            return self._heuristic
        if seat_kind == "llm":
            return LLMSeatAgent(
                model=str(seat.get("agent_model", "")),
                runtime_config=self._llm_runtime_config,
            )
        raise ValueError("Human seats do not have an autonomous agent runtime.")

    def decide(self, *, seat: dict[str, Any], snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> TurnDecision:
        """Delegate one turn decision to the appropriate autonomous seat policy."""

        agent = self.agent_for_seat(seat)
        return agent.decide_turn(snapshot=snapshot, tool_snapshot=tool_snapshot)

    def runtime_summary(self) -> dict[str, object]:
        """Return the public-safe autonomous-seat runtime summary for diagnostics."""

        return self._llm_runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE)

    def clear_llm_sessions(self, *, game_id: str, seats: list[dict[str, Any]]) -> None:
        """Best-effort cleanup of local encrypted session memory for LLM seats.

        Session TTL already limits stale data. This explicit cleanup is only for
        completed games so future maintainers can reason about session lifecycle
        without guessing whether seat memory persists indefinitely.
        """

        for seat in seats:
            if str(seat.get("seat_kind", "")).strip().lower() != "llm":
                continue
            LLMSeatAgent(
                model=str(seat.get("agent_model", "")),
                runtime_config=self._llm_runtime_config,
            ).clear_session(game_id=game_id, seat_id=str(seat.get("seat_id", "")))
