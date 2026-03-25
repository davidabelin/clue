"""Deterministic baseline seat agent for Clue."""

from __future__ import annotations

from typing import Any

from clue_agents.base import SeatAgent, TurnDecision


class HeuristicSeatAgent(SeatAgent):
    """Simple rules-first agent that leans on the deduction tool snapshot."""

    def decide_turn(self, *, snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> TurnDecision:
        legal = snapshot["legal_actions"]
        available = set(legal.get("available") or [])
        if "show_refute_card" in available:
            cards = legal.get("refute_cards") or []
            return TurnDecision(action="show_refute_card", card=str(cards[0]))
        if "pass_refute" in available:
            return TurnDecision(action="pass_refute")
        accusation = dict(tool_snapshot.get("accusation") or {})
        if "accuse" in available and accusation.get("should_accuse"):
            guess = dict(accusation.get("accusation") or {})
            return TurnDecision(action="accuse", **guess, rationale_private="Confidence threshold exceeded.")
        if "suggest" in available:
            ranked = list(tool_snapshot.get("suggestion_ranking") or [])
            if ranked:
                choice = ranked[0]
                return TurnDecision(
                    action="suggest",
                    suspect=str(choice["suspect"]),
                    weapon=str(choice["weapon"]),
                    rationale_private="Top ranked suggestion.",
                )
        if "move" in available:
            room_targets = [item for item in legal.get("move_targets") or [] if "_" not in str(item.get("node_id", ""))]
            targets = room_targets or list(legal.get("move_targets") or [])
            if targets:
                choice = sorted(targets, key=lambda item: (int(item.get("cost", 0)), str(item.get("label", ""))))[0]
                return TurnDecision(action="move", target_node=str(choice["node_id"]), rationale_private="Selected nearest legal move.")
        if "roll" in available:
            return TurnDecision(action="roll")
        return TurnDecision(action="end_turn")
