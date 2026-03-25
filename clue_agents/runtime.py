"""Seat-agent runtime selection and auto-play helpers."""

from __future__ import annotations

from typing import Any

from clue_agents.base import SeatAgent, TurnDecision
from clue_agents.heuristic import HeuristicSeatAgent
from clue_agents.llm import LLMSeatAgent


class AgentRuntime:
    def __init__(self) -> None:
        self._heuristic = HeuristicSeatAgent()

    def agent_for_seat(self, seat: dict[str, Any]) -> SeatAgent:
        seat_kind = str(seat.get("seat_kind", "human")).strip().lower()
        if seat_kind == "heuristic":
            return self._heuristic
        if seat_kind == "llm":
            return LLMSeatAgent(model=str(seat.get("agent_model", "")))
        raise ValueError("Human seats do not have an autonomous agent runtime.")

    def decide(self, *, seat: dict[str, Any], snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> TurnDecision:
        agent = self.agent_for_seat(seat)
        return agent.decide_turn(snapshot=snapshot, tool_snapshot=tool_snapshot)
