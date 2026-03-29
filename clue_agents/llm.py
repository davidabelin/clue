"""OpenAI-backed structured-output seat agent with heuristic fallback."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from clue_agents.base import SeatAgent, TurnDecision
from clue_agents.heuristic import HeuristicSeatAgent
from clue_agents.policy import accusation_window, persona_prompt, stock_public_comment
from clue_agents.secrets import resolve_openai_api_key
from clue_agents.safety import sanitize_public_chat

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - import guard for local test envs
    OpenAI = None


class LLMSeatAgent(SeatAgent):
    """Structured-output OpenAI seat agent with a deterministic fallback policy."""

    def __init__(self, *, model: str = "", api_key: str = "") -> None:
        """Capture runtime model settings and resolve the production API key if present."""

        self._model = str(model or os.getenv("CLUE_LLM_MODEL", "gpt-4o-mini")).strip()
        self._api_key = resolve_openai_api_key(api_key=api_key)
        self._fallback = HeuristicSeatAgent()
        self._timeout_seconds = max(float(os.getenv("CLUE_LLM_TIMEOUT_SECONDS", "12")), 1.0)

    def _fallback_decision(
        self,
        fallback: TurnDecision,
        *,
        snapshot: dict[str, Any],
        reason: str,
        tool_snapshot: dict[str, Any],
        error: Exception | None = None,
    ) -> TurnDecision:
        """Attach LLM-specific fallback metadata to the heuristic decision."""

        debug_private = dict(fallback.debug_private or {})
        debug_private.setdefault("belief_summary", dict(tool_snapshot.get("belief_summary") or {}))
        debug_private.setdefault("top_hypotheses", list(tool_snapshot.get("top_hypotheses") or [])[:3])
        debug_private.setdefault("top_ranked_suggestions", list(tool_snapshot.get("suggestion_ranking") or [])[:3])
        debug_private.setdefault("accusation_window", accusation_window(snapshot, tool_snapshot))
        if error is not None:
            debug_private["fallback_error"] = str(error)
        return TurnDecision(
            action=fallback.action,
            target_node=fallback.target_node,
            suspect=fallback.suspect,
            weapon=fallback.weapon,
            room=fallback.room,
            card=fallback.card,
            text=fallback.text,
            rationale_private=fallback.rationale_private,
            debug_private=debug_private,
            agent_meta={
                **dict(fallback.agent_meta or {}),
                "policy": "llm",
                "model": self._model,
                "persona": str(snapshot["seat"].get("character") or ""),
                "timeout_s": self._timeout_seconds,
                "fallback_used": True,
                "fallback_reason": reason,
            },
        )

    def decide_turn(self, *, snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> TurnDecision:
        """Ask the model for one structured turn decision, or fall back safely on any failure."""

        fallback = self._fallback.decide_turn(snapshot=snapshot, tool_snapshot=tool_snapshot)
        if OpenAI is None or not self._api_key:
            return self._fallback_decision(
                fallback,
                snapshot=snapshot,
                reason=("missing_openai_sdk" if OpenAI is None else "missing_api_key"),
                tool_snapshot=tool_snapshot,
            )
        accusation_gate = accusation_window(snapshot, tool_snapshot)
        legal = snapshot["legal_actions"]
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a Clue seat agent. Obey legal actions exactly. Use the tool summary as ground truth. "
                    "Never claim hidden card ownership in public chat. Keep rationale_private brief. "
                    f"Stay lightly in character as {snapshot['seat']['character']}: {persona_prompt(str(snapshot['seat']['character']))} "
                    "Public chat is optional and should be at most one short sentence about the current public action."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Seat: {snapshot['seat']['display_name']} ({snapshot['seat']['character']})\n"
                    f"Phase: {snapshot['phase']}\n"
                    f"Legal actions: {legal}\n"
                    f"Private hand: {snapshot['seat']['hand']}\n"
                    f"Tool snapshot: {tool_snapshot}\n"
                    f"Accusation pacing gate: {accusation_gate}\n"
                    "Return the single best next action."
                ),
            },
        ]
        try:
            client = OpenAI(api_key=self._api_key)
            started = time.perf_counter()
            response = client.responses.create(
                model=self._model,
                input=prompt,
                timeout=self._timeout_seconds,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "turn_decision",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string"},
                                "target_node": {"type": ["string", "null"]},
                                "suspect": {"type": ["string", "null"]},
                                "weapon": {"type": ["string", "null"]},
                                "room": {"type": ["string", "null"]},
                                "card": {"type": ["string", "null"]},
                                "text": {"type": ["string", "null"]},
                                "rationale_private": {"type": "string"},
                                "debug_private": {"type": ["object", "null"], "additionalProperties": True},
                            },
                            "required": ["action", "rationale_private"],
                            "additionalProperties": False,
                        },
                    }
                },
            )
            raw = json.loads(str(getattr(response, "output_text", "") or "{}"))
            decision = TurnDecision.from_dict(raw)
            if not decision.action:
                return self._fallback_decision(fallback, snapshot=snapshot, reason="empty_action", tool_snapshot=tool_snapshot)
            if not self._decision_is_legal(decision, legal):
                return self._fallback_decision(fallback, snapshot=snapshot, reason="illegal_action", tool_snapshot=tool_snapshot)
            if decision.action == "accuse" and not accusation_gate["ready"]:
                return self._fallback_decision(fallback, snapshot=snapshot, reason="accusation_hold", tool_snapshot=tool_snapshot)
            original_text = decision.text or ""
            if decision.text:
                decision.text = sanitize_public_chat(decision.text)
            if not decision.text:
                decision.text = stock_public_comment(
                    snapshot,
                    {
                        "action": decision.action,
                        "target_node": decision.target_node,
                        "suspect": decision.suspect,
                        "weapon": decision.weapon,
                        "room": decision.room,
                    },
                )
            decision.debug_private = {
                **dict(tool_snapshot.get("belief_summary") or {}),
                "top_hypotheses": list(tool_snapshot.get("top_hypotheses") or [])[:3],
                "top_ranked_suggestions": list(tool_snapshot.get("suggestion_ranking") or [])[:3],
                "accusation_window": accusation_gate,
                "model_rationale": decision.rationale_private,
            }
            decision.agent_meta = {
                "policy": "llm",
                "model": self._model,
                "persona": str(snapshot["seat"].get("character") or ""),
                "timeout_s": self._timeout_seconds,
                "fallback_used": False,
                "guardrail_blocked": bool(original_text and not decision.text),
                "decision_latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            }
            return decision
        except json.JSONDecodeError as exc:
            return self._fallback_decision(fallback, snapshot=snapshot, reason="malformed_json", tool_snapshot=tool_snapshot, error=exc)
        except TimeoutError as exc:
            return self._fallback_decision(fallback, snapshot=snapshot, reason="timeout", tool_snapshot=tool_snapshot, error=exc)
        except Exception as exc:
            return self._fallback_decision(fallback, snapshot=snapshot, reason="model_error", tool_snapshot=tool_snapshot, error=exc)

    @staticmethod
    def _decision_is_legal(decision: TurnDecision, legal: dict[str, Any]) -> bool:
        """Validate model output against the current legal-action envelope."""

        available = set(legal.get("available") or [])
        if decision.action not in available:
            return False
        if decision.action == "move":
            move_targets = {str(item.get("node_id")) for item in legal.get("move_targets") or []}
            return bool(decision.target_node) and str(decision.target_node) in move_targets
        if decision.action == "show_refute_card":
            refute_cards = {str(card) for card in legal.get("refute_cards") or []}
            return bool(decision.card) and str(decision.card) in refute_cards
        if decision.action == "suggest":
            return bool(decision.suspect and decision.weapon)
        if decision.action == "accuse":
            return bool(decision.suspect and decision.weapon and decision.room)
        return True
