"""OpenAI Agents SDK-backed Clue seat runtime.

This module deliberately keeps the model-facing seat logic separate from the
rules engine. The LLM may inspect seat-local context and produce one structured
decision, but the Clue ``GameMaster`` remains the only component allowed to
apply gameplay state changes. LLM seats fail loudly when the live model path is
unavailable; they do not fall back to the deterministic heuristic policy.
"""

from __future__ import annotations

import asyncio
from typing import Any

from clue_agents.base import ChatDecision, MemorySummaryDecision, SeatAgent, TurnDecision
from clue_agents.config import LLMRuntimeConfig, load_llm_runtime_config
from clue_agents.policy import accusation_window
from clue_agents.profile_loader import chat_model_profile, model_profile, model_runtime_defaults
from clue_agents.sdk_runtime import (
    AGENTS_SDK_AVAILABLE,
    AgentTurnOutput,
    ChatIntentOutput,
    ChatUtteranceOutput,
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    MemorySummaryOutput,
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


class MemorySummaryError(RuntimeError):
    """Raised when the LLM-only durable memory summary path cannot complete."""

    def __init__(self, reason: str, *, error: Exception | None = None) -> None:
        """Store a stable failure reason plus optional underlying exception."""

        self.reason = str(reason or "memory_summary_error")
        self.error = error
        message = self.reason if error is None else f"{self.reason}: {error}"
        super().__init__(message)


class LLMDecisionError(RuntimeError):
    """Raised when an LLM seat cannot produce a live turn or chat decision."""

    def __init__(
        self,
        reason: str,
        *,
        mode: str,
        error: Exception | None = None,
        debug: dict[str, Any] | None = None,
        runtime: dict[str, Any] | None = None,
    ) -> None:
        """Store a stable failure reason and private diagnostics for callers."""

        self.reason = str(reason or "llm_decision_error")
        self.mode = str(mode or "turn")
        self.error = error
        self.debug = dict(debug or {})
        self.runtime = dict(runtime or {})
        message = f"{self.mode}:{self.reason}" if error is None else f"{self.mode}:{self.reason}: {error}"
        super().__init__(message)


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
    """OpenAI Agents SDK seat policy that fails loudly when the model path fails.

    The LLM path is intentionally narrow:
    - read-only tools expose seat-local state
    - output guardrails validate legality and leakage risk
    - missing SDK/API credentials, guardrail blocks, timeouts, and model errors
      raise ``LLMDecisionError`` instead of producing a deterministic fake move

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

    def clear_session(self, *, game_id: str, seat_id: str) -> None:
        """Best-effort cleanup for one seat's local encrypted agent session."""

        if not AGENTS_SDK_AVAILABLE:
            return
        for session_id in (
            f"{str(game_id)}:{str(seat_id)}",
            f"{str(game_id)}:{str(seat_id)}:chat",
            f"{str(game_id)}:{str(seat_id)}:memory",
        ):
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

    def _memory_runtime_meta(self) -> dict[str, Any]:
        """Return the shared durable-memory runtime metadata surfaced on summaries."""

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

    def _raise_turn_error(
        self,
        reason: str,
        *,
        tool_snapshot: dict[str, Any],
        accusation_gate: dict[str, Any] | None = None,
        error: Exception | None = None,
        extra_debug: dict[str, Any] | None = None,
    ) -> None:
        """Raise one fail-loud turn error with private runtime diagnostics."""

        debug_private = {
            "belief_summary": dict(tool_snapshot.get("belief_summary") or {}),
            "top_hypotheses": list(tool_snapshot.get("top_hypotheses") or [])[:3],
            "top_ranked_suggestions": list(tool_snapshot.get("suggestion_ranking") or [])[:3],
            "accusation_window": dict(accusation_gate or {}),
        }
        if extra_debug:
            debug_private["llm_debug"] = dict(extra_debug)
        raise LLMDecisionError(
            reason,
            mode="turn",
            error=error,
            debug=debug_private,
            runtime=self._runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE),
        ) from error

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

    def _raise_chat_error(
        self,
        reason: str,
        *,
        error: Exception | None = None,
        extra_debug: dict[str, Any] | None = None,
    ) -> None:
        """Raise one fail-loud idle-chat error with private runtime diagnostics."""

        raise LLMDecisionError(
            reason,
            mode="chat",
            error=error,
            debug=dict(extra_debug or {}),
            runtime=self._chat_runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE),
        ) from error

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
                    else (
                        "Write the durable first-person memory summary for this completed Clue game."
                        if mode_label == "memory_summary"
                        else "Inspect the current seat-local state and return the single best next Clue action."
                    )
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
            else (
                ChatUtteranceOutput
                if mode_label == "chat_utterance"
                else (MemorySummaryOutput if mode_label == "memory_summary" else AgentTurnOutput)
            )
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
        """Ask the Agents SDK seat policy for one turn.

        Any missing dependency, runtime error, illegal output, or unsafe public
        text raises ``LLMDecisionError`` so the web runtime can stop and report
        the real LLM failure instead of impersonating an LLM with heuristic play.
        """

        if not AGENTS_SDK_AVAILABLE or not self._api_key:
            self._raise_turn_error(
                "missing_agents_sdk" if not AGENTS_SDK_AVAILABLE else "missing_api_key",
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
                self._raise_turn_error(
                    "tool_call_limit_exceeded",
                    tool_snapshot=tool_snapshot,
                    accusation_gate=accusation_gate,
                    extra_debug=artifacts,
                )
            if not decision.action:
                self._raise_turn_error(
                    "empty_action",
                    tool_snapshot=tool_snapshot,
                    accusation_gate=accusation_gate,
                    extra_debug=artifacts,
                )
            if not self._decision_is_legal(decision, legal):
                self._raise_turn_error(
                    "illegal_action",
                    tool_snapshot=tool_snapshot,
                    accusation_gate=accusation_gate,
                    extra_debug=artifacts,
                )
            if decision.action == "accuse" and not accusation_gate["ready"]:
                self._raise_turn_error(
                    "accusation_hold",
                    tool_snapshot=tool_snapshot,
                    accusation_gate=accusation_gate,
                    extra_debug=artifacts,
                )

            original_text = decision.text or ""
            sanitized_text = sanitize_public_chat(original_text)
            if original_text and not sanitized_text:
                self._raise_turn_error(
                    "unsafe_public_chat",
                    tool_snapshot=tool_snapshot,
                    accusation_gate=accusation_gate,
                    extra_debug=artifacts,
                )
            decision.text = sanitized_text
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
        except LLMDecisionError:
            raise
        except OutputGuardrailTripwireTriggered as exc:
            self._raise_turn_error(
                "output_guardrail_blocked",
                tool_snapshot=tool_snapshot,
                accusation_gate=accusation_gate,
                error=exc,
                extra_debug=guardrail_exception_payload(exc),
            )
        except InputGuardrailTripwireTriggered as exc:
            self._raise_turn_error(
                "input_guardrail_blocked",
                tool_snapshot=tool_snapshot,
                accusation_gate=accusation_gate,
                error=exc,
                extra_debug=guardrail_exception_payload(exc),
            )
        except MaxTurnsExceeded as exc:
            self._raise_turn_error(
                "max_turns_exceeded",
                tool_snapshot=tool_snapshot,
                accusation_gate=accusation_gate,
                error=exc,
            )
        except TimeoutError as exc:
            self._raise_turn_error(
                "timeout",
                tool_snapshot=tool_snapshot,
                accusation_gate=accusation_gate,
                error=exc,
            )
        except Exception as exc:
            self._raise_turn_error(
                "model_error",
                tool_snapshot=tool_snapshot,
                accusation_gate=accusation_gate,
                error=exc,
            )

    def summarize_memory(self, *, snapshot: dict[str, Any]) -> MemorySummaryDecision:
        """Ask the LLM to write durable first-person memory for a completed game.

        This path deliberately has no heuristic summary fallback. Callers should
        catch ``MemorySummaryError`` and leave the durable memory job queued for
        Administrator Mode retry.
        """

        if not AGENTS_SDK_AVAILABLE:
            raise MemorySummaryError("missing_agents_sdk")
        if not self._api_key:
            raise MemorySummaryError("missing_api_key")

        context = build_seat_context(
            snapshot=snapshot,
            tool_snapshot={},
            accusation_gate={"ready": False},
            runtime_config=self._chat_runtime_config,
            mode="memory_summary",
        )
        try:
            raw_output, artifacts = self._run_agent(context)
            decision = MemorySummaryDecision.from_dict(raw_output.model_dump())
            if not str(decision.summary.get("first_person_summary") or "").strip():
                raise MemorySummaryError("invalid_memory_summary")
            tool_call_count = len(list(artifacts.get("tool_calls") or []))
            decision.debug_private = {
                "mode": "llm_memory_summary",
                "llm_runtime": self._chat_runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE),
                "selected_profile_id": self._chat_profile_id,
                "selected_profile_label": self._chat_profile_label,
                "selected_turn_profile_id": self._profile_id,
                "selected_turn_profile_label": self._profile_label,
                "sdk_trace_id": str(artifacts.get("trace_id") or ""),
                "sdk_session_id": str(artifacts.get("session_id") or ""),
                "sdk_last_response_id": str(artifacts.get("last_response_id") or ""),
                "sdk_tool_calls": list(artifacts.get("tool_calls") or []),
                "sdk_output_guardrails": list(artifacts.get("output_guardrails") or []),
                "sdk_tool_input_guardrails": list(artifacts.get("tool_input_guardrails") or []),
                "model_debug_private": dict(getattr(raw_output, "debug_private", {}) or {}),
            }
            decision.agent_meta = {
                **self._memory_runtime_meta(),
                "fallback_used": False,
                "trace_id": str(artifacts.get("trace_id") or ""),
                "session_id": str(artifacts.get("session_id") or ""),
                "last_response_id": str(artifacts.get("last_response_id") or ""),
                "tool_call_count": tool_call_count,
                "tool_calls": list(artifacts.get("tool_calls") or []),
            }
            return decision
        except MemorySummaryError:
            raise
        except OutputGuardrailTripwireTriggered as exc:
            raise MemorySummaryError("output_guardrail_blocked", error=exc) from exc
        except InputGuardrailTripwireTriggered as exc:
            raise MemorySummaryError("input_guardrail_blocked", error=exc) from exc
        except MaxTurnsExceeded as exc:
            raise MemorySummaryError("max_turns_exceeded", error=exc) from exc
        except TimeoutError as exc:
            raise MemorySummaryError("timeout", error=exc) from exc
        except Exception as exc:
            raise MemorySummaryError("model_error", error=exc) from exc

    def decide_chat(self, *, snapshot: dict[str, Any]) -> ChatDecision:
        """Ask the Agents SDK seat policy for one idle-chat decision."""

        if not AGENTS_SDK_AVAILABLE or not self._api_key:
            self._raise_chat_error("missing_agents_sdk" if not AGENTS_SDK_AVAILABLE else "missing_api_key")

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
        except LLMDecisionError:
            raise
        except OutputGuardrailTripwireTriggered as exc:
            self._raise_chat_error(
                "output_guardrail_blocked",
                error=exc,
                extra_debug=guardrail_exception_payload(exc),
            )
        except InputGuardrailTripwireTriggered as exc:
            self._raise_chat_error(
                "input_guardrail_blocked",
                error=exc,
                extra_debug=guardrail_exception_payload(exc),
            )
        except MaxTurnsExceeded as exc:
            self._raise_chat_error("max_turns_exceeded", error=exc)
        except TimeoutError as exc:
            self._raise_chat_error("timeout", error=exc)
        except Exception as exc:
            self._raise_chat_error("model_error", error=exc)
