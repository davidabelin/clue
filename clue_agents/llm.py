"""OpenAI Agents SDK-backed Clue seat runtime with deterministic fallback.

This module deliberately keeps the model-facing seat logic separate from the
rules engine. The LLM may inspect seat-local context and produce one structured
decision, but the Clue ``GameMaster`` remains the only component allowed to
apply gameplay state changes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from clue_agents.base import ChatDecision, SeatAgent, TurnDecision
from clue_agents.config import LLMRuntimeConfig, load_llm_runtime_config
from clue_agents.heuristic import HeuristicSeatAgent
from clue_agents.policy import accusation_window, stock_public_comment
from clue_agents.profile_loader import chat_model_profile, model_profile, model_runtime_defaults
from clue_agents.sdk_runtime import (
    AGENTS_SDK_AVAILABLE,
    AgentTurnOutput,
    ChatIntentOutput,
    ChatUtteranceOutput,
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


_VALID_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}


def _runtime_config_with_profile(
    base_config: LLMRuntimeConfig,
    *,
    profile: dict[str, Any],
    model_override: str = "",
) -> LLMRuntimeConfig:
    """Apply one selected model-profile block to the normalized runtime config.

    This is the final merge step between env-level defaults, YAML runtime
    defaults, and explicit per-seat model overrides.
    """

    if not profile:
        return base_config.with_model_override(model_override)

    reasoning_effort = str(profile.get("reasoning_effort") or base_config.reasoning_effort).strip().lower()
    if reasoning_effort not in _VALID_REASONING_EFFORTS:
        reasoning_effort = base_config.reasoning_effort

    try:
        timeout_seconds = max(float(profile.get("timeout_seconds") or base_config.timeout_seconds), 1.0)
    except (TypeError, ValueError):
        timeout_seconds = base_config.timeout_seconds
    try:
        max_tool_calls = max(int(profile.get("max_tool_calls") or base_config.max_tool_calls), 1)
    except (TypeError, ValueError):
        max_tool_calls = base_config.max_tool_calls
    try:
        max_turns = max(int(profile.get("max_turns") or base_config.max_turns), max_tool_calls + 1)
    except (TypeError, ValueError):
        max_turns = max(base_config.max_turns, max_tool_calls + 1)

    chosen_model = str(model_override or profile.get("model") or base_config.model).strip() or base_config.model
    return LLMRuntimeConfig(
        model=chosen_model,
        reasoning_effort=reasoning_effort,
        timeout_seconds=timeout_seconds,
        max_tool_calls=max_tool_calls,
        max_turns=max_turns,
        tracing_enabled=base_config.tracing_enabled,
        trace_include_sensitive_data=base_config.trace_include_sensitive_data,
        session_ttl_seconds=base_config.session_ttl_seconds,
        session_db_path=base_config.session_db_path,
        session_encryption_key=base_config.session_encryption_key,
        eval_export_enabled=base_config.eval_export_enabled,
    )


class LLMSeatAgent(SeatAgent):
    """OpenAI Agents SDK seat policy with a deterministic heuristic fallback.

    The LLM path is intentionally narrow:
    - read-only tools expose seat-local state
    - output guardrails validate legality and leakage risk
    - the heuristic agent remains the fallback for any missing dependency,
      missing API key, guardrail block, timeout, or model failure

    This class is where runtime config, profile selection, local session ids,
    and normalized SDK output are converted into the seat-agent contract used by
    the rest of the repo.
    """

    def __init__(
        self,
        *,
        model: str = "",
        profile_id: str = "",
        chat_model: str = "",
        chat_profile_id: str = "",
        api_key: str = "",
        runtime_config: LLMRuntimeConfig | None = None,
    ) -> None:
        """Resolve runtime configuration and the API key for future turn decisions."""

        base_config = runtime_config or load_llm_runtime_config()
        turn_defaults = model_runtime_defaults(kind="turn")
        chat_defaults = model_runtime_defaults(kind="chat")
        self._profile_id = str(profile_id or "").strip()
        self._profile = model_profile(self._profile_id)
        self._profile_label = str(self._profile.get("public_label") or self._profile_id)
        turn_base_config = _runtime_config_with_profile(base_config, profile=turn_defaults, model_override="")
        self._runtime_config = _runtime_config_with_profile(turn_base_config, profile=self._profile, model_override=model)
        self._chat_profile_id = str(chat_profile_id or "").strip()
        self._chat_profile = chat_model_profile(self._chat_profile_id)
        self._chat_profile_label = str(self._chat_profile.get("public_label") or self._chat_profile_id)
        chat_model_override = str(chat_model or model or "").strip()
        chat_base_config = _runtime_config_with_profile(base_config, profile=chat_defaults, model_override="")
        self._chat_runtime_config = _runtime_config_with_profile(
            chat_base_config,
            profile=(self._chat_profile or self._profile),
            model_override=chat_model_override,
        )
        self._model = self._runtime_config.model
        self._chat_model = self._chat_runtime_config.model
        self._api_key = resolve_openai_api_key(api_key=api_key)
        self._fallback = HeuristicSeatAgent()

    def clear_session(self, *, game_id: str, seat_id: str) -> None:
        """Best-effort cleanup for one seat's local encrypted agent session."""

        if not AGENTS_SDK_AVAILABLE:
            return
        for session_id in (f"{str(game_id)}:{str(seat_id)}", f"{str(game_id)}:{str(seat_id)}:chat"):
            session = build_session_for_id(
                session_id,
                runtime_config=self._runtime_config,
            )
            asyncio.run(session.clear_session())

    def _turn_runtime_meta(self) -> dict[str, Any]:
        """Return the shared action-runtime metadata surfaced on turn decisions."""

        return {
            "policy": "llm",
            "backend": "openai_agents_sdk",
            "model": self._model,
            "reasoning_effort": self._runtime_config.reasoning_effort,
            "timeout_s": self._runtime_config.timeout_seconds,
            "profile_id": self._profile_id,
            "profile_label": self._profile_label,
            "chat_model": self._chat_model,
            "chat_profile_id": self._chat_profile_id,
            "chat_profile_label": self._chat_profile_label,
            "session_store": "local_encrypted_sqlalchemy_sqlite",
        }

    def _chat_runtime_meta(self) -> dict[str, Any]:
        """Return the shared chat-runtime metadata surfaced on chat decisions."""

        return {
            "policy": "llm",
            "backend": "openai_agents_sdk",
            "model": self._chat_model,
            "reasoning_effort": self._chat_runtime_config.reasoning_effort,
            "timeout_s": self._chat_runtime_config.timeout_seconds,
            "profile_id": self._chat_profile_id,
            "profile_label": self._chat_profile_label,
            "turn_model": self._model,
            "turn_profile_id": self._profile_id,
            "turn_profile_label": self._profile_label,
            "session_store": "local_encrypted_sqlalchemy_sqlite",
        }

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
        """Attach LLM-runtime diagnostics to the heuristic fallback decision.

        Fallback decisions should still explain why the model path was rejected
        so browser diagnostics and replay-style analysis remain actionable.
        """

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
                **self._turn_runtime_meta(),
                "fallback_used": True,
                "fallback_reason": reason,
                **dict(extra_meta or {}),
            },
        )

    def _silent_chat_decision(self, *, reason: str, extra_meta: dict[str, Any] | None = None) -> ChatDecision:
        """Return a deliberate no-post result for the chat path."""

        return ChatDecision(
            speak=False,
            text="",
            rationale_private="Silence was preferred over a risky or low-value chat line.",
            debug_private={"llm_runtime": self._chat_runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE)},
            agent_meta={
                **self._chat_runtime_meta(),
                "fallback_used": False,
                "fallback_reason": reason,
                **dict(extra_meta or {}),
            },
        )

    def _fallback_chat_decision(
        self,
        fallback: ChatDecision,
        *,
        reason: str,
        error: Exception | None = None,
        extra_debug: dict[str, Any] | None = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> ChatDecision:
        """Attach LLM-runtime diagnostics to the heuristic chat fallback."""

        debug_private = dict(fallback.debug_private or {})
        debug_private["llm_runtime"] = self._chat_runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE)
        if extra_debug:
            debug_private["llm_debug"] = dict(extra_debug)
        if error is not None:
            debug_private["fallback_error"] = str(error)
        return ChatDecision(
            speak=bool(fallback.speak),
            text=str(fallback.text or ""),
            rationale_private=str(fallback.rationale_private or ""),
            debug_private=debug_private,
            agent_meta={
                **dict(fallback.agent_meta or {}),
                **self._chat_runtime_meta(),
                "fallback_used": True,
                "fallback_reason": reason,
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

    def _run_agent(self, context) -> tuple[Any, dict[str, Any]]:
        """Execute one Agents SDK run and return the structured output plus artifacts."""

        if Runner is None:
            raise RuntimeError("OpenAI Agents SDK runner is unavailable.")
        mode_label = str(getattr(context, "mode", "turn") or "turn")
        is_chat = mode_label.startswith("chat")
        runtime_config = self._chat_runtime_config if is_chat else self._runtime_config
        agent = build_agent(runtime_config, mode=mode_label)
        session = build_session(context)
        result = Runner.run_sync(
            agent,
            (
                "Plan the next public social move for this autonomous Clue seat."
                if mode_label == "chat_intent"
                else (
                    "Write one safe, in-character public chat line for this autonomous Clue seat."
                    if mode_label == "chat_utterance"
                    else "Inspect the current seat-local state and return the single best next Clue action."
                )
            ),
            context=context,
            max_turns=runtime_config.max_turns,
            run_config=build_run_config(context, self._api_key),
            session=session,
        )
        output_type = (
            ChatIntentOutput
            if mode_label == "chat_intent"
            else (ChatUtteranceOutput if mode_label == "chat_utterance" else AgentTurnOutput)
        )
        output = result.final_output_as(output_type, raise_if_incorrect_type=True)
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
        """Ask the Agents SDK seat policy for one turn or fall back safely.

        The happy path is deliberately short. Any missing dependency, runtime
        error, illegal output, or unsafe public text routes back through the
        deterministic heuristic policy.
        """

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
            mode="turn",
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
                "selected_profile_id": self._profile_id,
                "selected_profile_label": self._profile_label,
                "selected_chat_profile_id": self._chat_profile_id,
                "selected_chat_profile_label": self._chat_profile_label,
                "sdk_trace_id": str(artifacts.get("trace_id") or ""),
                "sdk_session_id": str(artifacts.get("session_id") or ""),
                "sdk_last_response_id": str(artifacts.get("last_response_id") or ""),
                "sdk_tool_calls": list(artifacts.get("tool_calls") or []),
                "sdk_output_guardrails": list(artifacts.get("output_guardrails") or []),
                "sdk_tool_input_guardrails": list(artifacts.get("tool_input_guardrails") or []),
                "model_debug_private": dict(raw_output.debug_private or {}),
            }
            decision.agent_meta = {
                **self._turn_runtime_meta(),
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

    def decide_chat(self, *, snapshot: dict[str, Any]) -> ChatDecision:
        """Ask the Agents SDK seat policy for one idle-chat decision or fall back safely."""

        fallback = self._fallback.decide_chat(snapshot=snapshot)
        if not AGENTS_SDK_AVAILABLE or not self._api_key:
            return self._fallback_chat_decision(
                fallback,
                reason=("missing_agents_sdk" if not AGENTS_SDK_AVAILABLE else "missing_api_key"),
            )

        intent_context = build_seat_context(
            snapshot=snapshot,
            tool_snapshot={},
            accusation_gate={"ready": False},
            runtime_config=self._chat_runtime_config,
            mode="chat_intent",
        )
        try:
            raw_intent, intent_artifacts = self._run_agent(intent_context)
            shared_meta = {
                "session_id": str(intent_artifacts.get("session_id") or ""),
                "intent_trace_id": str(intent_artifacts.get("trace_id") or ""),
                "intent_last_response_id": str(intent_artifacts.get("last_response_id") or ""),
            }
            if hasattr(raw_intent, "text"):
                sanitized_text = sanitize_public_chat(str(getattr(raw_intent, "text", "") or ""))
                if not bool(getattr(raw_intent, "speak", False)):
                    silent = self._silent_chat_decision(reason="model_chose_silence", extra_meta=shared_meta)
                    silent.debug_private["legacy_chat_output"] = True
                    return silent
                if not sanitized_text:
                    silent = self._silent_chat_decision(reason="unsafe_public_chat", extra_meta=shared_meta)
                    silent.debug_private["legacy_chat_output"] = True
                    return silent
                return ChatDecision(
                    speak=True,
                    text=sanitized_text,
                    rationale_private=str(getattr(raw_intent, "rationale_private", "") or ""),
                    debug_private={
                        "mode": "llm_idle_chat_legacy",
                        "llm_runtime": self._chat_runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE),
                        "sdk_trace_id": str(intent_artifacts.get("trace_id") or ""),
                        "sdk_session_id": str(intent_artifacts.get("session_id") or ""),
                    },
                    agent_meta={
                        **self._chat_runtime_meta(),
                        "fallback_used": False,
                        "guardrail_blocked": bool(getattr(raw_intent, "text", "") and getattr(raw_intent, "text", "") != sanitized_text),
                        "trace_id": str(intent_artifacts.get("trace_id") or ""),
                        "session_id": str(intent_artifacts.get("session_id") or ""),
                        "last_response_id": str(intent_artifacts.get("last_response_id") or ""),
                        "tool_call_count": len(list(intent_artifacts.get("tool_calls") or [])),
                        "tool_calls": list(intent_artifacts.get("tool_calls") or []),
                    },
                )
            if not raw_intent.speak:
                silent = self._silent_chat_decision(reason="model_chose_silence", extra_meta=shared_meta)
                silent.debug_private["intent_rationale"] = raw_intent.rationale_private
                silent.debug_private["intent_plan"] = raw_intent.model_dump()
                silent.debug_private["sdk_intent_tool_calls"] = list(intent_artifacts.get("tool_calls") or [])
                silent.debug_private["sdk_output_guardrails"] = list(intent_artifacts.get("output_guardrails") or [])
                return silent

            utterance_context = build_seat_context(
                snapshot=snapshot,
                tool_snapshot={},
                accusation_gate={"ready": False},
                runtime_config=self._chat_runtime_config,
                mode="chat_utterance",
                chat_plan=raw_intent.model_dump(),
            )
            raw_utterance, utterance_artifacts = self._run_agent(utterance_context)
            sanitized_text = sanitize_public_chat(raw_utterance.text or "")
            combined_tool_calls = [*list(intent_artifacts.get("tool_calls") or []), *list(utterance_artifacts.get("tool_calls") or [])]
            shared_meta = {
                **shared_meta,
                "trace_id": str(utterance_artifacts.get("trace_id") or ""),
                "utterance_trace_id": str(utterance_artifacts.get("trace_id") or ""),
                "last_response_id": str(utterance_artifacts.get("last_response_id") or ""),
                "tool_call_count": len(combined_tool_calls),
                "tool_calls": combined_tool_calls,
            }
            if not sanitized_text:
                silent = self._silent_chat_decision(reason="unsafe_public_chat", extra_meta=shared_meta)
                silent.debug_private["intent_rationale"] = raw_intent.rationale_private
                silent.debug_private["utterance_rationale"] = raw_utterance.rationale_private
                silent.debug_private["intent_plan"] = raw_intent.model_dump()
                silent.debug_private["sdk_output_guardrails"] = [
                    *list(intent_artifacts.get("output_guardrails") or []),
                    *list(utterance_artifacts.get("output_guardrails") or []),
                ]
                return silent

            relationship_deltas = [item.model_dump() for item in list(raw_intent.relationship_deltas or [])]
            return ChatDecision(
                speak=True,
                text=sanitized_text,
                intent=raw_intent.intent,
                target_seat_id=str(raw_intent.target_seat_id or ""),
                topic=str(raw_intent.topic or ""),
                tone=str(raw_intent.tone or ""),
                thread_action=str(raw_intent.thread_action or ""),
                relationship_deltas=relationship_deltas,
                action_pressure_hint=str(raw_intent.action_pressure_hint or ""),
                rationale_private=str(raw_utterance.rationale_private or raw_intent.rationale_private or ""),
                debug_private={
                    "mode": "llm_idle_chat",
                    "llm_runtime": self._chat_runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE),
                    "selected_profile_id": self._chat_profile_id,
                    "selected_profile_label": self._chat_profile_label,
                    "selected_turn_profile_id": self._profile_id,
                    "selected_turn_profile_label": self._profile_label,
                    "chat_intent": raw_intent.model_dump(),
                    "relationship_deltas": relationship_deltas,
                    "sdk_trace_id": str(utterance_artifacts.get("trace_id") or ""),
                    "sdk_session_id": str(utterance_artifacts.get("session_id") or intent_artifacts.get("session_id") or ""),
                    "sdk_last_response_id": str(utterance_artifacts.get("last_response_id") or ""),
                    "sdk_intent_tool_calls": list(intent_artifacts.get("tool_calls") or []),
                    "sdk_utterance_tool_calls": list(utterance_artifacts.get("tool_calls") or []),
                    "sdk_output_guardrails": [
                        *list(intent_artifacts.get("output_guardrails") or []),
                        *list(utterance_artifacts.get("output_guardrails") or []),
                    ],
                    "sdk_tool_input_guardrails": [
                        *list(intent_artifacts.get("tool_input_guardrails") or []),
                        *list(utterance_artifacts.get("tool_input_guardrails") or []),
                    ],
                    "utterance_debug_private": dict(raw_utterance.debug_private or {}),
                },
                agent_meta={
                    **self._chat_runtime_meta(),
                    "fallback_used": False,
                    "guardrail_blocked": bool(raw_utterance.text and raw_utterance.text != sanitized_text),
                    **shared_meta,
                },
            )
        except OutputGuardrailTripwireTriggered as exc:
            return self._fallback_chat_decision(
                fallback,
                reason="output_guardrail_blocked",
                error=exc,
                extra_debug=guardrail_exception_payload(exc),
            )
        except InputGuardrailTripwireTriggered as exc:
            return self._fallback_chat_decision(
                fallback,
                reason="input_guardrail_blocked",
                error=exc,
                extra_debug=guardrail_exception_payload(exc),
            )
        except MaxTurnsExceeded as exc:
            return self._fallback_chat_decision(fallback, reason="max_turns_exceeded", error=exc)
        except TimeoutError as exc:
            return self._fallback_chat_decision(fallback, reason="timeout", error=exc)
        except Exception as exc:
            return self._fallback_chat_decision(fallback, reason="model_error", error=exc)
