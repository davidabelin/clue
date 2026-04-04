"""Deterministic baseline seat agent for Clue."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from clue_agents.base import ChatDecision, SeatAgent, TurnDecision
from clue_agents.policy import (
    accusation_window,
    current_seat_social_state,
    current_social_thread,
    event_actor_seat_id,
    public_chat_events,
    stock_idle_chat,
    stock_public_comment,
)
from clue_agents.profile_loader import persona_metric
from clue_core.board import NODE_TO_ROOM_NAME, ROOM_NAME_TO_NODE, shortest_paths


class HeuristicSeatAgent(SeatAgent):
    """Simple rules-first agent that leans on the deduction tool snapshot."""

    @staticmethod
    def _persona_value(snapshot: dict[str, Any], field_name: str, *, default: int = 3) -> int:
        """Read one persona slider for the acting seat from the YAML catalog."""

        return persona_metric(str(snapshot["seat"].get("character") or ""), field_name, default=default)

    @staticmethod
    def _decision_context(tool_snapshot: dict[str, Any]) -> dict[str, Any]:
        """Collect the high-signal deduction context carried into one action."""

        return {
            "belief_summary": dict(tool_snapshot.get("belief_summary") or {}),
            "top_hypotheses": list(tool_snapshot.get("top_hypotheses") or [])[:3],
            "opponent_model": dict(tool_snapshot.get("opponent_model") or {}),
            "accusation": dict(tool_snapshot.get("accusation") or {}),
        }

    @staticmethod
    def _room_priorities(tool_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Summarize which rooms currently look best for the next information-seeking move."""

        scores: defaultdict[str, float] = defaultdict(float)
        room_marginals = dict((tool_snapshot.get("envelope_marginals") or {}).get("room") or {})
        for room_name, probability in room_marginals.items():
            scores[str(room_name)] += float(probability) * 1.35
        for hypothesis in tool_snapshot.get("top_hypotheses") or []:
            room_name = str(hypothesis.get("room") or "")
            if room_name:
                scores[room_name] += float(hypothesis.get("p") or 0.0) * 1.7
        ranked = [
            {"room": room_name, "score": round(score, 4)}
            for room_name, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        ]
        return ranked

    @staticmethod
    def _recent_room_history(snapshot: dict[str, Any]) -> list[str]:
        """Extract the rooms this seat has recently entered or suggested from."""

        seat_id = str(snapshot["seat"]["seat_id"])
        history: list[str] = []
        for event in snapshot.get("events") or []:
            event_type = str(event.get("event_type") or "")
            payload = dict(event.get("payload") or {})
            if event_type == "suggestion_made" and str(payload.get("seat_id") or "") == seat_id:
                room_name = str((payload.get("suggestion") or {}).get("room") or "")
                if room_name:
                    history.append(room_name)
            if event_type == "moved" and str(payload.get("seat_id") or "") == seat_id:
                room_name = NODE_TO_ROOM_NAME.get(str(payload.get("to") or ""))
                if room_name:
                    history.append(room_name)
        return history[-4:]

    def _pick_move_target(self, snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Choose a move target that aims toward the most informative reachable room."""

        legal_targets = list(snapshot["legal_actions"].get("move_targets") or [])
        if not legal_targets:
            return None, {"move_evaluations": []}

        ranked_rooms = self._room_priorities(tool_snapshot)
        recent_rooms = self._recent_room_history(snapshot)
        current_room = str(snapshot["legal_actions"].get("current_room") or "")
        fallback_target = sorted(legal_targets, key=lambda item: (int(item.get("cost", 0)), str(item.get("label", ""))))[0]
        if not ranked_rooms:
            return fallback_target, {
                "move_evaluations": [],
                "fallback_move": dict(fallback_target),
                "recent_rooms": recent_rooms,
            }

        evaluations = []
        for target in legal_targets:
            node_id = str(target.get("node_id") or "")
            node_room = NODE_TO_ROOM_NAME.get(node_id, "")
            distances = shortest_paths(node_id)
            best_goal_room = ""
            best_goal_score = -999.0
            for room_entry in ranked_rooms[:5]:
                room_name = str(room_entry["room"])
                room_weight = float(room_entry["score"])
                distance = int(distances.get(ROOM_NAME_TO_NODE[room_name], 99))
                goal_score = (room_weight * 2.4) + (max(0, 8 - distance) * 0.12)
                if node_room == room_name:
                    goal_score += 0.55
                if room_name == current_room:
                    goal_score -= 0.2
                if room_name in recent_rooms[-2:]:
                    goal_score -= 0.36
                if goal_score > best_goal_score:
                    best_goal_score = goal_score
                    best_goal_room = room_name
            total_score = best_goal_score - (float(target.get("cost") or 0) * 0.08)
            if node_room and node_room not in recent_rooms[-2:]:
                total_score += 0.18
            if str(target.get("mode") or "") == "passage":
                total_score += 0.12 if best_goal_room and best_goal_room != current_room else -0.22
            evaluations.append(
                {
                    **dict(target),
                    "node_room": node_room,
                    "goal_room": best_goal_room,
                    "score": round(total_score, 4),
                }
            )

        choice = max(
            evaluations,
            key=lambda item: (
                float(item["score"]),
                -float(item.get("cost") or 0),
                str(item.get("label") or ""),
            ),
        )
        if snapshot.get("phase") == "start_turn" and str(choice.get("mode") or "") == "passage":
            current_score = next((float(item["score"]) for item in ranked_rooms if str(item["room"]) == current_room), 0.0)
            target_score = next((float(item["score"]) for item in ranked_rooms if str(item["room"]) == str(choice.get("goal_room") or "")), 0.0)
            if target_score + 0.08 < current_score:
                return None, {
                    "preferred_room": current_room,
                    "move_evaluations": evaluations[:5],
                    "recent_rooms": recent_rooms,
                }
        return choice, {
            "move_evaluations": evaluations[:5],
            "recent_rooms": recent_rooms,
            "ranked_rooms": ranked_rooms[:5],
        }

    def _pick_refute_card(self, snapshot: dict[str, Any], cards: list[str]) -> tuple[str, dict[str, Any]]:
        """Choose the least revealing legal refute card using public-history hints."""

        if not cards:
            return "", {"refute_card_mentions": {}}
        concealment = self._persona_value(snapshot, "concealment_priority", default=3)
        mention_counts: defaultdict[str, int] = defaultdict(int)
        for event in snapshot.get("events") or []:
            payload = dict(event.get("payload") or {})
            suggestion = dict(payload.get("suggestion") or {})
            for card_name in suggestion.values():
                mention_counts[str(card_name)] += 1
        ranked = sorted(
            cards,
            key=lambda card_name: (
                -(mention_counts.get(str(card_name), 0) * max(concealment, 1)),
                str(card_name),
            ),
        )
        return str(ranked[0]), {"refute_card_mentions": dict(mention_counts), "refute_choice_ranked": ranked}

    def _pick_suggestion(self, snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Choose a suggestion using deduction rank plus social-pressure tie-breaks."""

        ranked = list(tool_snapshot.get("suggestion_ranking") or [])
        if not ranked:
            return None, {"suggestion_evaluations": []}
        focus_seat_id = str(current_seat_social_state(snapshot).get("focus_seat_id") or "")
        pressure_weight = self._persona_value(snapshot, "opponent_pressure", default=3) / 5.0
        image_weight = self._persona_value(snapshot, "table_image_management", default=3) / 5.0
        concealment_weight = self._persona_value(snapshot, "concealment_priority", default=3) / 5.0
        evaluations = []
        for choice in ranked[:5]:
            likely_refuter = str(choice.get("likely_refuter") or "")
            score = float(choice.get("score") or 0.0)
            pressure_bonus = 0.0
            image_bonus = 0.0
            concealment_penalty = float(choice.get("opponent_leak_penalty") or 0.0) * concealment_weight * 0.18
            if likely_refuter and likely_refuter != "unanswered":
                pressure_bonus += 0.08 * pressure_weight
            if focus_seat_id and likely_refuter == focus_seat_id:
                pressure_bonus += 0.2 * pressure_weight
            image_bonus += float(choice.get("unanswered_probability") or 0.0) * 0.12 * image_weight
            total = score + pressure_bonus + image_bonus - concealment_penalty
            evaluations.append(
                dict(choice)
                | {
                    "persona_pressure_bonus": round(pressure_bonus, 4),
                    "persona_image_bonus": round(image_bonus, 4),
                    "persona_concealment_penalty": round(concealment_penalty, 4),
                    "persona_total_score": round(total, 4),
                }
            )
        selected = max(
            evaluations,
            key=lambda item: (
                float(item["persona_total_score"]),
                float(item.get("score") or 0.0),
                str(item.get("suspect") or ""),
                str(item.get("weapon") or ""),
            ),
        )
        return selected, {"suggestion_evaluations": evaluations, "focus_seat_id": focus_seat_id}

    def decide_turn(self, *, snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> TurnDecision:
        """Pick a deterministic fallback action using only legal moves and tool hints."""

        legal = snapshot["legal_actions"]
        available = set(legal.get("available") or [])
        debug = self._decision_context(tool_snapshot)
        accusation_gate = accusation_window(snapshot, tool_snapshot)
        debug["accusation_window"] = accusation_gate
        agent_meta = {
            "policy": "heuristic",
            "fallback_used": False,
            "persona": str(snapshot["seat"].get("character") or ""),
        }
        if "show_refute_card" in available:
            cards = legal.get("refute_cards") or []
            selected_card, refute_debug = self._pick_refute_card(snapshot, [str(card) for card in cards])
            return TurnDecision(
                action="show_refute_card",
                card=selected_card,
                rationale_private="Show the least revealing legal refutation card.",
                debug_private=debug | {"selected_refute_card": selected_card} | refute_debug,
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
        risk_tolerance = self._persona_value(snapshot, "risk_tolerance", default=3)
        accusation_patience = self._persona_value(snapshot, "accusation_patience", default=3)
        if "accuse" in available and accusation_gate["ready"]:
            if "suggest" in available and not accusation_gate["lock_case"] and (accusation_patience >= 4 or risk_tolerance <= 2):
                debug["persona_accusation_hold"] = {
                    "risk_tolerance": risk_tolerance,
                    "accusation_patience": accusation_patience,
                    "reason": "held accusation for one more information-seeking action",
                }
            else:
                guess = dict(accusation.get("accusation") or {})
                return TurnDecision(
                    action="accuse",
                    **guess,
                    text=stock_public_comment(snapshot, {"action": "accuse", **guess}),
                    rationale_private="Top case-file hypothesis cleared the accusation threshold.",
                    debug_private=debug | {"chosen_accusation": guess},
                    agent_meta=agent_meta,
                )
        if "suggest" in available:
            choice, suggestion_debug = self._pick_suggestion(snapshot, tool_snapshot)
            if choice:
                return TurnDecision(
                    action="suggest",
                    suspect=str(choice["suspect"]),
                    weapon=str(choice["weapon"]),
                    text=stock_public_comment(
                        snapshot,
                        {
                            "action": "suggest",
                            "suspect": str(choice["suspect"]),
                            "weapon": str(choice["weapon"]),
                            "room": str(choice.get("room") or legal.get("current_room") or ""),
                        },
                    ),
                    rationale_private=str(choice.get("why") or "Top ranked suggestion."),
                    debug_private=debug
                    | {
                        "selected_suggestion": choice,
                        "top_ranked_suggestions": list(tool_snapshot.get("suggestion_ranking") or [])[:3],
                    }
                    | suggestion_debug,
                    agent_meta=agent_meta,
                )
        if "move" in available:
            choice, move_debug = self._pick_move_target(snapshot, tool_snapshot)
            if choice:
                return TurnDecision(
                    action="move",
                    target_node=str(choice["node_id"]),
                    text=stock_public_comment(snapshot, {"action": "move", "target_node": str(choice["node_id"])}),
                    rationale_private="Moved toward the most informative reachable room.",
                    debug_private=debug | {"chosen_move": dict(choice), "move_target_count": len(legal.get("move_targets") or [])} | move_debug,
                    agent_meta=agent_meta,
                )
        if "roll" in available:
            return TurnDecision(
                action="roll",
                rationale_private="Open the turn with a roll to widen movement options.",
                debug_private=debug,
                agent_meta=agent_meta,
            )
        return TurnDecision(action="end_turn", rationale_private="No stronger legal action remained.", debug_private=debug, agent_meta=agent_meta)

    def decide_chat(self, *, snapshot: dict[str, Any]) -> ChatDecision:
        """Compose one short deterministic public chat line from visible table context."""

        text = stock_idle_chat(snapshot)
        seat_social = current_seat_social_state(snapshot)
        thread = current_social_thread(snapshot)
        latest_chat = public_chat_events(snapshot)
        latest_actor = event_actor_seat_id(latest_chat[-1]) if latest_chat else ""
        intent = {
            "dispute": "reconcile" if str(thread.get("status") or "") == "cooling" else "challenge",
            "alliance": "ally",
            "flirtation": "tease",
            "meta": "meta_observe",
            "banter": "tease",
        }.get(str(thread.get("kind") or ""), "deflect" if latest_chat else "meta_observe")
        target_seat_id = str(seat_social.get("focus_seat_id") or latest_actor or "")
        tone = {
            "challenge": "cutting",
            "ally": "warm",
            "reconcile": "measured",
            "tease": "wry",
            "deflect": "guarded",
            "meta_observe": "dry",
        }.get(intent, "dry")
        relationship_deltas = []
        if target_seat_id and target_seat_id != str(snapshot["seat"].get("seat_id") or ""):
            if intent == "challenge":
                relationship_deltas.append({"seat_id": target_seat_id, "friction_delta": 1})
            elif intent == "ally":
                relationship_deltas.append({"seat_id": target_seat_id, "affinity_delta": 1, "trust_delta": 1})
            elif intent == "reconcile":
                relationship_deltas.append({"seat_id": target_seat_id, "trust_delta": 1, "friction_delta": -1})
        return ChatDecision(
            speak=bool(text),
            text=text,
            intent=intent,
            target_seat_id=target_seat_id,
            topic=str(thread.get("topic") or ""),
            tone=tone,
            thread_action=("continue" if thread else ("open" if target_seat_id else "observe")),
            relationship_deltas=relationship_deltas,
            action_pressure_hint=("keep pressure on " + target_seat_id) if target_seat_id and intent == "challenge" else "",
            thread_id=str(thread.get("thread_id") or ""),
            rationale_private="Responded with a deterministic in-character table-talk line.",
            debug_private={"mode": "heuristic_idle_chat", "thread": dict(thread), "seat_social": dict(seat_social)},
            agent_meta={"policy": "heuristic", "fallback_used": False, "persona": str(snapshot["seat"].get("character") or "")},
        )
