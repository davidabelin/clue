"""Deterministic baseline seat agent for Clue."""

from __future__ import annotations

from typing import Any

from clue_agents.base import SeatAgent, TurnDecision


class HeuristicSeatAgent(SeatAgent):
    """Simple rules-first agent that leans on the deduction tool snapshot."""

    @staticmethod
    def _decision_context(tool_snapshot: dict[str, Any]) -> dict[str, Any]:
        """Collect the high-signal deduction context carried into one action."""

        return {
            "belief_summary": dict(tool_snapshot.get("belief_summary") or {}),
            "top_hypotheses": list(tool_snapshot.get("top_hypotheses") or [])[:3],
            "opponent_model": dict(tool_snapshot.get("opponent_model") or {}),
            "accusation": dict(tool_snapshot.get("accusation") or {}),
        }

    def decide_turn(self, *, snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> TurnDecision:
        """Pick a deterministic fallback action using only legal moves and tool hints."""

        legal = snapshot["legal_actions"]
        available = set(legal.get("available") or [])
        debug = self._decision_context(tool_snapshot)
        agent_meta = {"policy": "heuristic", "fallback_used": False}
        if "show_refute_card" in available:
            cards = legal.get("refute_cards") or []
            return TurnDecision(
                action="show_refute_card",
                card=str(cards[0]),
                rationale_private="Show the first legal refutation card.",
                debug_private=debug | {"selected_refute_card": str(cards[0])},
                agent_meta=agent_meta,
            )
        if "pass_refute" in available:
            return TurnDecision(
                action="pass_refute",
                rationale_private="No legal refutation card is available.",
                debug_private=debug,
                agent_meta=agent_meta,
            )
        accusation = dict(tool_snapshot.get("accusation") or {})
        if "accuse" in available and accusation.get("should_accuse"):
            guess = dict(accusation.get("accusation") or {})
            return TurnDecision(
                action="accuse",
                **guess,
                rationale_private="Top case-file hypothesis cleared the accusation threshold.",
                debug_private=debug | {"chosen_accusation": guess},
                agent_meta=agent_meta,
            )
        if "suggest" in available:
            ranked = list(tool_snapshot.get("suggestion_ranking") or [])
            if ranked:
                choice = ranked[0]
                return TurnDecision(
                    action="suggest",
                    suspect=str(choice["suspect"]),
                    weapon=str(choice["weapon"]),
                    rationale_private=str(choice.get("why") or "Top ranked suggestion."),
                    debug_private=debug | {"selected_suggestion": choice, "top_ranked_suggestions": ranked[:3]},
                    agent_meta=agent_meta,
                )
        if "move" in available:
            room_targets = [item for item in legal.get("move_targets") or [] if "_" not in str(item.get("node_id", ""))]
            targets = room_targets or list(legal.get("move_targets") or [])
            if targets:
                choice = sorted(targets, key=lambda item: (int(item.get("cost", 0)), str(item.get("label", ""))))[0]
                return TurnDecision(
                    action="move",
                    target_node=str(choice["node_id"]),
                    rationale_private="Selected the nearest legal move target.",
                    debug_private=debug | {"chosen_move": dict(choice), "move_target_count": len(targets)},
                    agent_meta=agent_meta,
                )
        if "roll" in available:
            return TurnDecision(action="roll", rationale_private="Open the turn with a roll.", debug_private=debug, agent_meta=agent_meta)
        return TurnDecision(action="end_turn", rationale_private="No stronger legal action remained.", debug_private=debug, agent_meta=agent_meta)
