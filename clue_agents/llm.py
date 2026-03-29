"""OpenAI Agents SDK-backed Clue seat runtime with deterministic fallback.

This module deliberately keeps the model-facing seat logic separate from the
rules engine. The LLM may inspect seat-local context and produce one structured
decision, but the Clue ``GameMaster`` remains the only component allowed to
apply gameplay state changes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from clue_agents.base import SeatAgent, TurnDecision
from clue_agents.config import LLMRuntimeConfig, load_llm_runtime_config
from clue_agents.heuristic import HeuristicSeatAgent
from clue_agents.policy import accusation_window, stock_public_comment
from clue_agents.sdk_runtime import (
    AGENTS_SDK_AVAILABLE,
    AgentTurnOutput,
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    OutputGuardrailTripwireTriggered,
    Runner,
    build_agent,
    build_artifacts,
    build_run_config,
    build_seat_context,
    build_session,
    build_session_for_id,
    guardrail_exception_payload,
)
from clue_agents.secrets import resolve_openai_api_key
from clue_agents.safety import sanitize_public_chat


class LLMSeatAgent(SeatAgent):
    """OpenAI Agents SDK seat policy with a deterministic heuristic fallback.

    The LLM path is intentionally narrow:
    - read-only tools expose seat-local state
    - output guardrails validate legality and leakage risk
    - the heuristic agent remains the fallback for any missing dependency,
      missing API key, guardrail block, timeout, or model failure
    """

    def __init__(
        self,
        *,
        model: str = "",
        api_key: str = "",
        runtime_config: LLMRuntimeConfig | None = None,
    ) -> None:
        """Resolve runtime configuration and the API key for future turn decisions."""

        base_config = runtime_config or load_llm_runtime_config()
        self._runtime_config = base_config.with_model_override(model)
        self._model = self._runtime_config.model
        self._api_key = resolve_openai_api_key(api_key=api_key)
        self._fallback = HeuristicSeatAgent()

    def clear_session(self, *, game_id: str, seat_id: str) -> None:
        """Best-effort cleanup for one seat's local encrypted agent session."""

        if not AGENTS_SDK_AVAILABLE:
            return
        session = build_session_for_id(
            f"{str(game_id)}:{str(seat_id)}",
            runtime_config=self._runtime_config,
        )
        asyncio.run(session.clear_session())

    def _fallback_decision(
        self,
        fallback: TurnDecision,
        *,
        snapshot: dict[str, Any],
        reason: str,
        tool_snapshot: dict[str, Any],
        error: Exception | None = None,
        extra_debug: dict[str, Any] | None = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> TurnDecision:
        """Attach LLM-runtime diagnostics to the heuristic fallback decision."""

        debug_private = dict(fallback.debug_private or {})
        debug_private.setdefault("belief_summary", dict(tool_snapshot.get("belief_summary") or {}))
        debug_private.setdefault("top_hypotheses", list(tool_snapshot.get("top_hypotheses") or [])[:3])
        debug_private.setdefault("top_ranked_suggestions", list(tool_snapshot.get("suggestion_ranking") or [])[:3])
        debug_private.setdefault("accusation_window", accusation_window(snapshot, tool_snapshot))
        debug_private["llm_runtime"] = self._runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE)
        if extra_debug:
            debug_private["llm_debug"] = dict(extra_debug)
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
                "backend": "openai_agents_sdk",
                "model": self._model,
                "reasoning_effort": self._runtime_config.reasoning_effort,
                "timeout_s": self._runtime_config.timeout_seconds,
                "fallback_used": True,
                "fallback_reason": reason,
                "session_store": "local_encrypted_sqlalchemy_sqlite",
                **dict(extra_meta or {}),
            },
        )

    @staticmethod
    def _decision_is_legal(decision: TurnDecision, legal: dict[str, Any]) -> bool:
        """Validate the normalized decision against the current legal envelope."""

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

    def _run_agent(self, context) -> tuple[AgentTurnOutput, dict[str, Any]]:
        """Execute one Agents SDK run and return the structured output plus artifacts."""

        if Runner is None:
            raise RuntimeError("OpenAI Agents SDK runner is unavailable.")
        agent = build_agent(self._runtime_config)
        session = build_session(context)
        result = Runner.run_sync(
            agent,
            "Inspect the current seat-local state and return the single best next Clue action.",
            context=context,
            max_turns=self._runtime_config.max_turns,
            run_config=build_run_config(context, self._api_key),
            session=session,
        )
        output = result.final_output_as(AgentTurnOutput, raise_if_incorrect_type=True)
        artifacts = build_artifacts(result, context)
        return output, {
            "trace_id": artifacts.trace_id,
            "session_id": artifacts.session_id,
            "last_response_id": artifacts.last_response_id,
            "tool_calls": artifacts.tool_calls,
            "output_guardrails": artifacts.output_guardrails,
            "tool_input_guardrails": artifacts.tool_input_guardrails,
        }

    def decide_turn(self, *, snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> TurnDecision:
        """Ask the Agents SDK seat policy for one turn or fall back safely."""

        fallback = self._fallback.decide_turn(snapshot=snapshot, tool_snapshot=tool_snapshot)
        if not AGENTS_SDK_AVAILABLE or not self._api_key:
            return self._fallback_decision(
                fallback,
                snapshot=snapshot,
                reason=("missing_agents_sdk" if not AGENTS_SDK_AVAILABLE else "missing_api_key"),
                tool_snapshot=tool_snapshot,
            )

        accusation_gate = accusation_window(snapshot, tool_snapshot)
        legal = dict(snapshot.get("legal_actions") or {})
        context = build_seat_context(
            snapshot=snapshot,
            tool_snapshot=tool_snapshot,
            accusation_gate=accusation_gate,
            runtime_config=self._runtime_config,
        )
        try:
            raw_output, artifacts = self._run_agent(context)
            decision = TurnDecision.from_dict(raw_output.model_dump())
            tool_call_count = len(list(artifacts.get("tool_calls") or []))
            if tool_call_count > self._runtime_config.max_tool_calls:
                return self._fallback_decision(
                    fallback,
                    snapshot=snapshot,
                    reason="tool_call_limit_exceeded",
                    tool_snapshot=tool_snapshot,
                    extra_debug=artifacts,
                    extra_meta={"trace_id": artifacts.get("trace_id", ""), "session_id": artifacts.get("session_id", "")},
                )
            if not decision.action:
                return self._fallback_decision(
                    fallback,
                    snapshot=snapshot,
                    reason="empty_action",
                    tool_snapshot=tool_snapshot,
                    extra_debug=artifacts,
                )
            if not self._decision_is_legal(decision, legal):
                return self._fallback_decision(
                    fallback,
                    snapshot=snapshot,
                    reason="illegal_action",
                    tool_snapshot=tool_snapshot,
                    extra_debug=artifacts,
                )
            if decision.action == "accuse" and not accusation_gate["ready"]:
                return self._fallback_decision(
                    fallback,
                    snapshot=snapshot,
                    reason="accusation_hold",
                    tool_snapshot=tool_snapshot,
                    extra_debug=artifacts,
                )

            original_text = decision.text or ""
            sanitized_text = sanitize_public_chat(original_text)
            if original_text and not sanitized_text:
                return self._fallback_decision(
                    fallback,
                    snapshot=snapshot,
                    reason="unsafe_public_chat",
                    tool_snapshot=tool_snapshot,
                    extra_debug=artifacts,
                )
            decision.text = sanitized_text or stock_public_comment(
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
                "model_rationale": raw_output.rationale_private,
                "llm_runtime": self._runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE),
                "sdk_trace_id": str(artifacts.get("trace_id") or ""),
                "sdk_session_id": str(artifacts.get("session_id") or ""),
                "sdk_last_response_id": str(artifacts.get("last_response_id") or ""),
                "sdk_tool_calls": list(artifacts.get("tool_calls") or []),
                "sdk_output_guardrails": list(artifacts.get("output_guardrails") or []),
                "sdk_tool_input_guardrails": list(artifacts.get("tool_input_guardrails") or []),
                "model_debug_private": dict(raw_output.debug_private or {}),
            }
            decision.agent_meta = {
                "policy": "llm",
                "backend": "openai_agents_sdk",
                "model": self._model,
                "reasoning_effort": self._runtime_config.reasoning_effort,
                "timeout_s": self._runtime_config.timeout_seconds,
                "fallback_used": False,
                "guardrail_blocked": bool(original_text and original_text != sanitized_text),
                "trace_id": str(artifacts.get("trace_id") or ""),
                "session_id": str(artifacts.get("session_id") or ""),
                "last_response_id": str(artifacts.get("last_response_id") or ""),
                "tool_call_count": tool_call_count,
                "tool_calls": list(artifacts.get("tool_calls") or []),
            }
            return decision
        except OutputGuardrailTripwireTriggered as exc:
            return self._fallback_decision(
                fallback,
                snapshot=snapshot,
                reason="output_guardrail_blocked",
                tool_snapshot=tool_snapshot,
                error=exc,
                extra_debug=guardrail_exception_payload(exc),
            )
        except InputGuardrailTripwireTriggered as exc:
            return self._fallback_decision(
                fallback,
                snapshot=snapshot,
                reason="input_guardrail_blocked",
                tool_snapshot=tool_snapshot,
                error=exc,
                extra_debug=guardrail_exception_payload(exc),
            )
        except MaxTurnsExceeded as exc:
            return self._fallback_decision(
                fallback,
                snapshot=snapshot,
                reason="max_turns_exceeded",
                tool_snapshot=tool_snapshot,
                error=exc,
            )
        except TimeoutError as exc:
            return self._fallback_decision(
                fallback,
                snapshot=snapshot,
                reason="timeout",
                tool_snapshot=tool_snapshot,
                error=exc,
            )
        except Exception as exc:
            return self._fallback_decision(
                fallback,
                snapshot=snapshot,
                reason="model_error",
                tool_snapshot=tool_snapshot,
                error=exc,
            )
