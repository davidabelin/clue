"""OpenAI Agents SDK helpers for the Clue LLM seat runtime.

The game service and deterministic rules engine should not need to know how the
Agents SDK is wired together. This module keeps that integration boundary small:
typed output, read-only tools, local session handling, guardrails, and the
diagnostic metadata needed by future maintainers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from clue_agents.config import LLMRuntimeConfig
from clue_agents.policy import persona_prompt
from clue_agents.safety import sanitize_public_chat

try:
    from agents import Agent, ModelSettings, RunConfig, Runner, function_tool, output_guardrail
    from agents.exceptions import (
        InputGuardrailTripwireTriggered,
        MaxTurnsExceeded,
        OutputGuardrailTripwireTriggered,
    )
    from agents.extensions.memory import EncryptedSession, SQLAlchemySession
    from agents.guardrail import GuardrailFunctionOutput, OutputGuardrailResult
    from agents.items import ToolCallItem
    from agents.models.openai_provider import OpenAIProvider
    from agents.run_context import RunContextWrapper
    from agents.tool_guardrails import (
        ToolGuardrailFunctionOutput,
        ToolInputGuardrailData,
        ToolInputGuardrailResult,
        tool_input_guardrail,
    )

    AGENTS_SDK_AVAILABLE = True
except Exception:  # pragma: no cover - import guard for local or CI environments
    Agent = None
    ModelSettings = None
    RunConfig = None
    Runner = None
    function_tool = None
    output_guardrail = None
    EncryptedSession = None
    SQLAlchemySession = None
    OpenAIProvider = None
    RunContextWrapper = None
    ToolCallItem = None
    GuardrailFunctionOutput = None
    ToolGuardrailFunctionOutput = None
    OutputGuardrailResult = None
    ToolInputGuardrailResult = None
    InputGuardrailTripwireTriggered = Exception
    OutputGuardrailTripwireTriggered = Exception
    MaxTurnsExceeded = Exception
    AGENTS_SDK_AVAILABLE = False


class AgentTurnOutput(BaseModel):
    """Structured output contract returned by the Agents SDK seat agent."""

    model_config = ConfigDict(extra="forbid")

    action: str
    target_node: str | None = None
    suspect: str | None = None
    weapon: str | None = None
    room: str | None = None
    card: str | None = None
    text: str | None = None
    rationale_private: str = Field(default="")
    debug_private: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class SeatAgentContext:
    """Code-owned per-turn context passed into the Agents SDK run.

    This object is the private source of truth for tool data. It allows tools and
    guardrails to inspect seat-local state without expanding the model-visible
    prompt with unnecessary hidden details.
    """

    runtime_config: LLMRuntimeConfig
    snapshot: dict[str, Any]
    tool_snapshot: dict[str, Any]
    accusation_gate: dict[str, Any]
    trace_id: str
    session_id: str
    tool_access_log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def game_id(self) -> str:
        """Return the current game id, falling back to a stable test label."""

        return str(self.snapshot.get("game_id") or "test_game")

    @property
    def seat_id(self) -> str:
        """Return the acting seat id."""

        return str((self.snapshot.get("seat") or {}).get("seat_id") or "unknown_seat")

    @property
    def legal_actions(self) -> dict[str, Any]:
        """Return the current legal action envelope."""

        return dict(self.snapshot.get("legal_actions") or {})

    @property
    def notebook_text(self) -> str:
        """Return the current private notebook text visible to this seat."""

        return str((self.snapshot.get("notebook") or {}).get("text") or "")

    def record_tool_call(self, name: str, *, arguments: dict[str, Any] | None = None) -> None:
        """Append one tool invocation summary for diagnostics and test assertions."""

        self.tool_access_log.append(
            {
                "name": name,
                "arguments": dict(arguments or {}),
            }
        )

    def move_target_map(self) -> dict[str, dict[str, Any]]:
        """Index legal move options by target node id for guardrails and tools."""

        return {
            str(item.get("node_id")): dict(item)
            for item in self.legal_actions.get("move_targets") or []
            if item.get("node_id")
        }

    def refute_card_set(self) -> set[str]:
        """Return the current legal refutation cards, if any."""

        return {str(card) for card in self.legal_actions.get("refute_cards") or [] if card}


@dataclass(slots=True)
class SeatRunArtifacts:
    """Diagnostics captured from one Agents SDK seat run."""

    trace_id: str
    session_id: str
    last_response_id: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    output_guardrails: list[dict[str, Any]] = field(default_factory=list)
    tool_input_guardrails: list[dict[str, Any]] = field(default_factory=list)


def build_seat_context(
    *,
    snapshot: dict[str, Any],
    tool_snapshot: dict[str, Any],
    accusation_gate: dict[str, Any],
    runtime_config: LLMRuntimeConfig,
) -> SeatAgentContext:
    """Build the private context object for one seat-agent run."""

    seat = dict(snapshot.get("seat") or {})
    game_id = str(snapshot.get("game_id") or "test_game")
    seat_id = str(seat.get("seat_id") or "unknown_seat")
    turn_index = int(snapshot.get("turn_index") or 0)
    trace_id = f"clue-{game_id}-{seat_id}-{turn_index}-{uuid.uuid4().hex[:10]}"
    session_id = f"{game_id}:{seat_id}"
    return SeatAgentContext(
        runtime_config=runtime_config,
        snapshot=snapshot,
        tool_snapshot=tool_snapshot,
        accusation_gate=accusation_gate,
        trace_id=trace_id,
        session_id=session_id,
    )


def _tool_guardrail_payload(data: ToolInputGuardrailData) -> dict[str, Any]:
    """Parse one tool call's raw JSON argument string for validation logic."""

    try:
        parsed = json.loads(str(data.context.tool_arguments or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


if AGENTS_SDK_AVAILABLE:

    @tool_input_guardrail(name="clue_move_target_scope_guardrail")
    def move_target_scope_guardrail(data: ToolInputGuardrailData) -> ToolGuardrailFunctionOutput:
        """Reject move-target lookups that are outside the current legal envelope."""

        context = data.context.context
        payload = _tool_guardrail_payload(data)
        target_node = str(payload.get("target_node") or "")
        if target_node in context.move_target_map():
            return ToolGuardrailFunctionOutput.allow(output_info={"target_node": target_node})
        return ToolGuardrailFunctionOutput.reject_content(
            "That move target is not legal for the current seat-local state.",
            output_info={"target_node": target_node, "allowed": sorted(context.move_target_map().keys())},
        )


    @tool_input_guardrail(name="clue_refute_card_scope_guardrail")
    def refute_card_scope_guardrail(data: ToolInputGuardrailData) -> ToolGuardrailFunctionOutput:
        """Reject refute-card lookups that are outside the current private refute options."""

        context = data.context.context
        payload = _tool_guardrail_payload(data)
        card_name = str(payload.get("card") or "")
        if card_name in context.refute_card_set():
            return ToolGuardrailFunctionOutput.allow(output_info={"card": card_name})
        return ToolGuardrailFunctionOutput.reject_content(
            "That card is not in the current private refute set.",
            output_info={"card": card_name, "allowed": sorted(context.refute_card_set())},
        )


    @function_tool
    def get_legal_action_envelope(context: RunContextWrapper[SeatAgentContext]) -> dict[str, Any]:
        """Return the legal action envelope for the current turn."""

        context.context.record_tool_call("get_legal_action_envelope")
        return dict(context.context.legal_actions)


    @function_tool
    def get_board_room_summary(context: RunContextWrapper[SeatAgentContext]) -> dict[str, Any]:
        """Return the current phase, position, room, and available action names."""

        context.context.record_tool_call("get_board_room_summary")
        seat = dict(context.context.snapshot.get("seat") or {})
        legal = context.context.legal_actions
        return {
            "phase": str(context.context.snapshot.get("phase") or ""),
            "position": str(seat.get("position") or ""),
            "current_room": str(legal.get("current_room") or ""),
            "available_actions": list(legal.get("available") or []),
            "remaining_steps": int(legal.get("remaining_steps") or 0),
            "roll_value": legal.get("roll_value"),
        }


    @function_tool
    def get_belief_summary(context: RunContextWrapper[SeatAgentContext]) -> dict[str, Any]:
        """Return the top-level deduction and entropy summary for this seat."""

        context.context.record_tool_call("get_belief_summary")
        return dict(context.context.tool_snapshot.get("belief_summary") or {})


    @function_tool
    def get_top_hypotheses(context: RunContextWrapper[SeatAgentContext]) -> list[dict[str, Any]]:
        """Return the top case-file hypotheses available to this seat."""

        context.context.record_tool_call("get_top_hypotheses")
        return list(context.context.tool_snapshot.get("top_hypotheses") or [])[:5]


    @function_tool
    def get_ranked_suggestions(context: RunContextWrapper[SeatAgentContext]) -> list[dict[str, Any]]:
        """Return the highest-ranked suggestion candidates for the current room."""

        context.context.record_tool_call("get_ranked_suggestions")
        return list(context.context.tool_snapshot.get("suggestion_ranking") or [])[:5]


    @function_tool
    def get_accusation_recommendation(context: RunContextWrapper[SeatAgentContext]) -> dict[str, Any]:
        """Return the current accusation recommendation and confidence metrics."""

        context.context.record_tool_call("get_accusation_recommendation")
        return dict(context.context.tool_snapshot.get("accusation") or {})


    @function_tool
    def read_private_notebook(context: RunContextWrapper[SeatAgentContext]) -> dict[str, Any]:
        """Return the current seat's notebook excerpt for local reasoning continuity."""

        context.context.record_tool_call("read_private_notebook")
        note = context.context.notebook_text
        return {
            "text": note[:1200],
            "has_content": bool(note.strip()),
            "character_count": len(note),
        }


    @function_tool(tool_input_guardrails=[move_target_scope_guardrail])
    def inspect_move_target(
        context: RunContextWrapper[SeatAgentContext],
        target_node: str,
    ) -> dict[str, Any]:
        """Return one legal move target by node id."""

        context.context.record_tool_call("inspect_move_target", arguments={"target_node": target_node})
        return dict(context.context.move_target_map().get(str(target_node), {}))


    @function_tool(tool_input_guardrails=[refute_card_scope_guardrail])
    def inspect_refute_card(
        context: RunContextWrapper[SeatAgentContext],
        card: str,
    ) -> dict[str, Any]:
        """Return one legal private refute card when the seat is in a refutation window."""

        context.context.record_tool_call("inspect_refute_card", arguments={"card": card})
        return {"card": str(card), "allowed": str(card) in context.context.refute_card_set()}


    @output_guardrail(name="clue_turn_output_guardrail")
    def clue_turn_output_guardrail(
        context: RunContextWrapper[SeatAgentContext],
        _agent: Agent[SeatAgentContext],
        agent_output: AgentTurnOutput,
    ) -> GuardrailFunctionOutput:
        """Block unsafe or illegal seat outputs before they reach the rules engine."""

        issues: list[str] = []
        legal = context.context.legal_actions
        available = {str(name) for name in legal.get("available") or []}

        if str(agent_output.action) not in available:
            issues.append("action_outside_legal_envelope")
        if agent_output.action == "move":
            if str(agent_output.target_node or "") not in context.context.move_target_map():
                issues.append("illegal_move_target")
        if agent_output.action == "show_refute_card":
            if str(agent_output.card or "") not in context.context.refute_card_set():
                issues.append("illegal_refute_card")
        if agent_output.action == "suggest" and not (agent_output.suspect and agent_output.weapon):
            issues.append("suggestion_missing_fields")
        if agent_output.action == "accuse":
            if not (agent_output.suspect and agent_output.weapon and agent_output.room):
                issues.append("accusation_missing_fields")
            if not context.context.accusation_gate.get("ready"):
                issues.append("accusation_gate_not_ready")

        sanitized_text = sanitize_public_chat(agent_output.text or "")
        if agent_output.text and not sanitized_text:
            issues.append("unsafe_public_chat")

        return GuardrailFunctionOutput(
            output_info={
                "issues": issues,
                "action": agent_output.action,
                "sanitized_text": sanitized_text,
            },
            tripwire_triggered=bool(issues),
        )


def build_agent(runtime_config: LLMRuntimeConfig) -> Agent[SeatAgentContext]:
    """Construct the single-turn Clue seat agent definition for one run."""

    if not AGENTS_SDK_AVAILABLE:
        raise RuntimeError("OpenAI Agents SDK is not available in this environment.")
    return Agent(
        name="Clue Seat Agent",
        instructions=_agent_instructions,
        tools=[
            get_legal_action_envelope,
            get_board_room_summary,
            get_belief_summary,
            get_top_hypotheses,
            get_ranked_suggestions,
            get_accusation_recommendation,
            read_private_notebook,
            inspect_move_target,
            inspect_refute_card,
        ],
        model=runtime_config.model,
        model_settings=ModelSettings(
            tool_choice="required",
            parallel_tool_calls=False,
            max_tokens=420,
            reasoning={"effort": runtime_config.reasoning_effort},
            verbosity="low",
            store=False,
            extra_args={"timeout": runtime_config.timeout_seconds},
            metadata={
                "app": "clue",
                "release": runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE)["release_label"],  # type: ignore[index]
            },
        ),
        output_type=AgentTurnOutput,
        output_guardrails=[clue_turn_output_guardrail],
    )


def _agent_instructions(context: RunContextWrapper[SeatAgentContext], _agent: Agent[SeatAgentContext]) -> str:
    """Build the seat-specific instruction block for one agent run."""

    seat = dict(context.context.snapshot.get("seat") or {})
    legal = context.context.legal_actions
    available = ", ".join(str(item) for item in legal.get("available") or []) or "none"
    current_room = str(legal.get("current_room") or "not currently in a room")
    return (
        "You are the autonomous seat agent for one player in the board game Clue.\n"
        "The deterministic rules engine is authoritative. You must choose exactly one legal action.\n"
        "You may only use the read-only tools provided. They summarize seat-local private state.\n"
        "Never invent new facts, never mention another seat's hidden card ownership, and never expose private information in public text.\n"
        f"Seat: {seat.get('display_name', 'Unknown')} ({seat.get('character', 'Unknown')}).\n"
        f"In-character public voice: {persona_prompt(str(seat.get('character') or ''))}\n"
        f"Phase: {context.context.snapshot.get('phase', '')}. Current room: {current_room}.\n"
        f"Available actions: {available}.\n"
        f"Notebook has content: {bool(context.context.notebook_text.strip())}.\n"
        f"Accusation gate ready: {bool(context.context.accusation_gate.get('ready'))}.\n"
        "Call at least one relevant tool before returning. Keep rationale_private short and useful for maintainers."
    )


def build_run_config(context: SeatAgentContext, api_key: str) -> RunConfig:
    """Build the run configuration for one local-first Clue seat-agent turn."""

    if not AGENTS_SDK_AVAILABLE:
        raise RuntimeError("OpenAI Agents SDK is not available in this environment.")
    return RunConfig(
        model_provider=OpenAIProvider(api_key=api_key, use_responses=True),
        tracing_disabled=not context.runtime_config.tracing_enabled,
        trace_include_sensitive_data=context.runtime_config.trace_include_sensitive_data,
        workflow_name="Clue Seat Decision",
        trace_id=context.trace_id,
        group_id=context.game_id,
        trace_metadata={
            "app": "clue",
            "game_id": context.game_id,
            "seat_id": context.seat_id,
            "release": context.runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE)["release_label"],  # type: ignore[index]
        },
    )


def build_session(context: SeatAgentContext):
    """Build the encrypted local session used to persist seat-private agent memory."""

    return build_session_for_id(context.session_id, runtime_config=context.runtime_config)


def build_session_for_id(session_id: str, *, runtime_config: LLMRuntimeConfig):
    """Build one encrypted session wrapper for a known session id."""

    if not AGENTS_SDK_AVAILABLE:
        raise RuntimeError("OpenAI Agents SDK is not available in this environment.")
    underlying = SQLAlchemySession.from_url(
        session_id,
        url=runtime_config.session_db_url,
        create_tables=True,
    )
    return EncryptedSession(
        session_id=session_id,
        underlying_session=underlying,
        encryption_key=runtime_config.session_encryption_key,
        ttl=runtime_config.session_ttl_seconds,
    )


def build_artifacts(result: Any, context: SeatAgentContext) -> SeatRunArtifacts:
    """Convert the raw SDK run result into Clue-facing diagnostics metadata."""

    output_guardrails: list[dict[str, Any]] = []
    for item in list(getattr(result, "output_guardrail_results", []) or []):
        output_guardrails.append(
            {
                "name": item.guardrail.get_name(),
                "tripwire_triggered": bool(item.output.tripwire_triggered),
                "output_info": item.output.output_info,
            }
        )
    tool_input_guardrails: list[dict[str, Any]] = []
    for item in list(getattr(result, "tool_input_guardrail_results", []) or []):
        tool_input_guardrails.append(
            {
                "name": item.guardrail.get_name(),
                "behavior": dict(item.output.behavior),
                "output_info": item.output.output_info,
            }
        )
    tool_calls = list(context.tool_access_log)
    if ToolCallItem is not None and not tool_calls:
        for run_item in list(getattr(result, "new_items", []) or []):
            if isinstance(run_item, ToolCallItem):
                raw_item = getattr(run_item, "raw_item", None)
                if raw_item is not None and hasattr(raw_item, "name"):
                    tool_calls.append(
                        {
                            "name": str(getattr(raw_item, "name", "") or ""),
                            "arguments": {},
                            "source": "sdk_run_items",
                        }
                    )
    return SeatRunArtifacts(
        trace_id=str(getattr(getattr(result, "trace", None), "trace_id", "") or context.trace_id),
        session_id=context.session_id,
        last_response_id=str(getattr(result, "last_response_id", "") or ""),
        tool_calls=tool_calls,
        output_guardrails=output_guardrails,
        tool_input_guardrails=tool_input_guardrails,
    )


def guardrail_exception_payload(exc: Exception) -> dict[str, Any]:
    """Normalize guardrail exceptions into one serializable debug payload."""

    if isinstance(exc, OutputGuardrailTripwireTriggered):
        guardrail_result: OutputGuardrailResult = exc.guardrail_result
        return {
            "kind": "output_guardrail",
            "name": guardrail_result.guardrail.get_name(),
            "output_info": guardrail_result.output.output_info,
        }
    if isinstance(exc, InputGuardrailTripwireTriggered):
        return {
            "kind": "input_guardrail",
            "name": exc.guardrail_result.guardrail.get_name(),
            "output_info": exc.guardrail_result.output.output_info,
        }
    return {"kind": "guardrail", "name": exc.__class__.__name__, "output_info": {"error": str(exc)}}
