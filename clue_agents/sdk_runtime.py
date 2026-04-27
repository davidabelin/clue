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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from clue_agents.config import LLMRuntimeConfig
from clue_agents.policy import persona_prompt, public_chat_events, public_narrative_events, social_prompt
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


CHAT_INTENTS = {"tease", "deflect", "challenge", "ally", "reconcile", "meta_observe"}
CHAT_TONES = {"playful", "warm", "cutting", "guarded", "measured", "dry", "flirtatious", "confident", "wry"}
CHAT_THREAD_ACTIONS = {"open", "continue", "resolve", "observe"}


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


class AgentChatOutput(BaseModel):
    """Structured output contract returned by the Agents SDK chat agent."""

    model_config = ConfigDict(extra="forbid")

    speak: bool
    text: str = Field(default="")
    rationale_private: str = Field(default="")
    debug_private: dict[str, Any] = Field(default_factory=dict)


class RelationshipDeltaOutput(BaseModel):
    """One bounded relationship adjustment emitted by the chat-intent planner."""

    model_config = ConfigDict(extra="forbid")

    seat_id: str
    affinity_delta: int = Field(default=0, ge=-2, le=2)
    trust_delta: int = Field(default=0, ge=-2, le=2)
    friction_delta: int = Field(default=0, ge=-2, le=2)


class ChatIntentOutput(BaseModel):
    """Structured social intent emitted before the public chat line is written."""

    model_config = ConfigDict(extra="forbid")

    speak: bool
    intent: Literal["tease", "deflect", "challenge", "ally", "reconcile", "meta_observe"] = "meta_observe"
    target_seat_id: str = ""
    topic: str = ""
    tone: Literal["playful", "warm", "cutting", "guarded", "measured", "dry", "flirtatious", "confident", "wry"] = (
        "dry"
    )
    thread_action: Literal["open", "continue", "resolve", "observe"] = "observe"
    relationship_deltas: list[RelationshipDeltaOutput] = Field(default_factory=list)
    action_pressure_hint: str = Field(default="")
    rationale_private: str = Field(default="")


class ChatUtteranceOutput(BaseModel):
    """Structured public line emitted after the chat intent has been chosen."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="")
    rationale_private: str = Field(default="")
    debug_private: dict[str, Any] = Field(default_factory=dict)


class MemoryRelationshipUpdateOutput(BaseModel):
    """One durable relationship update emitted by a completed-game memory run."""

    model_config = ConfigDict(extra="forbid")

    target_kind: Literal["nhp", "hp"]
    target_identity: str
    target_display_name: str = ""
    affinity_delta: int = Field(default=0, ge=-2, le=2)
    trust_delta: int = Field(default=0, ge=-2, le=2)
    friction_delta: int = Field(default=0, ge=-2, le=2)
    note: str = ""


class MemorySummaryOutput(BaseModel):
    """Structured durable memory summary emitted after a game completes."""

    model_config = ConfigDict(extra="forbid")

    first_person_summary: str
    strategic_lessons: list[str] = Field(default_factory=list)
    social_observations: list[str] = Field(default_factory=list)
    grudges: list[str] = Field(default_factory=list)
    favors: list[str] = Field(default_factory=list)
    future_play_cues: list[str] = Field(default_factory=list)
    relationship_updates: list[MemoryRelationshipUpdateOutput] = Field(default_factory=list)
    rationale_private: str = Field(default="")
    debug_private: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class SeatAgentContext:
    """Code-owned per-turn context passed into the Agents SDK run.

    This object is the private source of truth for tool data. It allows tools and
    guardrails to inspect seat-local state without expanding the model-visible
    prompt with unnecessary hidden details. The same object also captures trace
    ids, session ids, and tool-access logs so the runtime can surface useful
    diagnostics without giving the model direct write access to them.
    """

    runtime_config: LLMRuntimeConfig
    snapshot: dict[str, Any]
    tool_snapshot: dict[str, Any]
    accusation_gate: dict[str, Any]
    trace_id: str
    session_id: str
    mode: str = "turn"
    chat_plan: dict[str, Any] = field(default_factory=dict)
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

    @property
    def public_chat_history(self) -> list[dict[str, Any]]:
        """Return the most recent public chat events visible to the seat."""

        return list(public_chat_events(self.snapshot))[-8:]

    @property
    def public_narrative_history(self) -> list[dict[str, Any]]:
        """Return the most recent player-facing public narrative events."""

        return list(public_narrative_events(self.snapshot))[-6:]

    @property
    def social_state(self) -> dict[str, Any]:
        """Return the current seat's social-memory summary."""

        return dict((self.snapshot.get("social") or {}).get("seat_state") or {})

    @property
    def active_threads(self) -> list[dict[str, Any]]:
        """Return the currently visible social threads involving this seat."""

        return list((self.snapshot.get("social") or {}).get("active_threads") or [])[-6:]

    @property
    def memory_context(self) -> dict[str, Any]:
        """Return durable cross-game memory context injected by the game service."""

        return dict(self.snapshot.get("memory_context") or {})

    @property
    def public_seat_map(self) -> dict[str, dict[str, Any]]:
        """Index public seat records by seat id for chat validation and prompts."""

        seats = {
            str(item.get("seat_id") or ""): dict(item)
            for item in self.snapshot.get("seats") or []
            if str(item.get("seat_id") or "")
        }
        current = dict(self.snapshot.get("seat") or {})
        if str(current.get("seat_id") or ""):
            seats[str(current["seat_id"])] = current
        return seats

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
    mode: str = "turn",
    chat_plan: dict[str, Any] | None = None,
) -> SeatAgentContext:
    """Build the private context object for one seat-agent run.

    Context construction is the join point between the filtered snapshot, the
    deduction summary, accusation pacing, and the mode-specific session id.
    """

    seat = dict(snapshot.get("seat") or {})
    game_id = str(snapshot.get("game_id") or "test_game")
    seat_id = str(seat.get("seat_id") or "unknown_seat")
    turn_index = int(snapshot.get("turn_index") or 0)
    requested_mode = str(mode).strip().lower()
    mode_label = requested_mode if requested_mode in {"chat", "chat_intent", "chat_utterance", "memory_summary"} else "turn"
    trace_id = f"clue-{game_id}-{seat_id}-{mode_label}-{turn_index}-{uuid.uuid4().hex[:10]}"
    session_id = (
        f"{game_id}:{seat_id}:chat"
        if mode_label.startswith("chat")
        else (f"{game_id}:{seat_id}:memory" if mode_label == "memory_summary" else f"{game_id}:{seat_id}")
    )
    return SeatAgentContext(
        runtime_config=runtime_config,
        snapshot=snapshot,
        tool_snapshot=tool_snapshot,
        accusation_gate=accusation_gate,
        trace_id=trace_id,
        session_id=session_id,
        mode=mode_label,
        chat_plan=dict(chat_plan or {}),
    )


def _tool_guardrail_payload(data: ToolInputGuardrailData) -> dict[str, Any]:
    """Parse one tool call's raw JSON argument string for validation logic."""

    try:
        parsed = json.loads(str(data.context.tool_arguments or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


if AGENTS_SDK_AVAILABLE:

    def _chat_target_known(context: SeatAgentContext, seat_id: str) -> bool:
        """Report whether one seat id is visible in the current public seat map."""

        return str(seat_id).strip() in context.public_seat_map


    def _fabricated_public_fact_issue(context: SeatAgentContext, text: str) -> str:
        """Detect obvious public-history claims unsupported by the recent visible window."""

        normalized = str(text or "").strip().lower()
        if not normalized:
            return ""
        fact_keywords = (
            "rolled",
            "moved",
            "suggested",
            "accused",
            "refuted",
            "could not refute",
            "couldn't refute",
            "used a secret passage",
        )
        if not any(keyword in normalized for keyword in fact_keywords):
            return ""
        recent_messages = " ".join(
            str(event.get("message") or "").strip().lower()
            for event in [*context.public_chat_history, *context.public_narrative_history]
        )
        named_seats = [
            str(seat.get("display_name") or "").strip().lower()
            for seat in context.public_seat_map.values()
            if str(seat.get("display_name") or "").strip()
        ]
        names_in_text = [name for name in named_seats if name in normalized]
        keyword_hits = [keyword for keyword in fact_keywords if keyword in normalized]
        if names_in_text and not any(name in recent_messages for name in names_in_text):
            return "fabricated_public_fact_reference"
        if keyword_hits and not any(keyword in recent_messages for keyword in keyword_hits):
            return "fabricated_public_fact_reference"
        return ""

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


    @function_tool
    def get_social_state_summary(context: RunContextWrapper[SeatAgentContext]) -> dict[str, Any]:
        """Return the current seat's code-owned social-memory summary."""

        context.context.record_tool_call("get_social_state_summary")
        return dict(context.context.social_state)


    @function_tool
    def get_active_chat_threads(context: RunContextWrapper[SeatAgentContext]) -> list[dict[str, Any]]:
        """Return the visible active chat threads involving this seat."""

        context.context.record_tool_call("get_active_chat_threads")
        return list(context.context.active_threads)


    @function_tool
    def get_relationship_posture(
        context: RunContextWrapper[SeatAgentContext],
        target_seat_id: str = "",
    ) -> dict[str, Any]:
        """Return the current seat's relationship posture toward one public seat."""

        context.context.record_tool_call("get_relationship_posture", arguments={"target_seat_id": target_seat_id})
        relationships = dict(context.context.social_state.get("relationships") or {})
        target_id = str(target_seat_id or "").strip()
        if not target_id:
            return {"relationships": relationships}
        target = dict(context.context.public_seat_map.get(target_id) or {})
        return {
            "target_seat_id": target_id,
            "target_display_name": str(target.get("display_name") or ""),
            "target_character": str(target.get("character") or ""),
            "posture": dict(relationships.get(target_id) or {}),
        }


    @function_tool
    def get_recent_public_chat_turns(
        context: RunContextWrapper[SeatAgentContext],
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Return the latest visible public chat turns up to the requested limit."""

        context.context.record_tool_call("get_recent_public_chat_turns", arguments={"limit": limit})
        safe_limit = min(max(int(limit), 1), 12)
        return list(context.context.public_chat_history)[-safe_limit:]


    @function_tool
    def get_recent_public_narrative_turns(
        context: RunContextWrapper[SeatAgentContext],
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        """Return the latest visible player-facing narrative turns."""

        context.context.record_tool_call("get_recent_public_narrative_turns", arguments={"limit": limit})
        safe_limit = min(max(int(limit), 1), 12)
        return list(context.context.public_narrative_history)[-safe_limit:]


    @function_tool
    def get_durable_memory_context(context: RunContextWrapper[SeatAgentContext]) -> dict[str, Any]:
        """Return ready cross-game memory and durable relationship context for this NHP."""

        context.context.record_tool_call("get_durable_memory_context")
        return dict(context.context.memory_context)


    @function_tool
    def get_final_game_context(context: RunContextWrapper[SeatAgentContext]) -> dict[str, Any]:
        """Return a compact completed-game summary for memory-authoring runs."""

        context.context.record_tool_call("get_final_game_context")
        snapshot = context.context.snapshot
        seat = dict(snapshot.get("seat") or {})
        seats = [
            {
                "seat_id": item.get("seat_id"),
                "display_name": item.get("display_name"),
                "character": item.get("character"),
                "seat_kind": item.get("seat_kind"),
                "can_win": item.get("can_win"),
            }
            for item in list(snapshot.get("seats") or [])
        ]
        return {
            "game_id": snapshot.get("game_id"),
            "title": snapshot.get("title"),
            "status": snapshot.get("status"),
            "turn_index": snapshot.get("turn_index"),
            "winner_seat_id": snapshot.get("winner_seat_id"),
            "seat": {
                "seat_id": seat.get("seat_id"),
                "display_name": seat.get("display_name"),
                "character": seat.get("character"),
                "seat_kind": seat.get("seat_kind"),
                "can_win": seat.get("can_win"),
                "hand": list(seat.get("hand") or []),
            },
            "seats": seats,
            "recent_public_narrative": list(context.context.public_narrative_history)[-12:],
            "recent_public_chat": list(context.context.public_chat_history)[-12:],
        }


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


    @output_guardrail(name="clue_chat_output_guardrail")
    def clue_chat_output_guardrail(
        context: RunContextWrapper[SeatAgentContext],
        _agent: Agent[SeatAgentContext],
        agent_output: AgentChatOutput,
    ) -> GuardrailFunctionOutput:
        """Block unsafe public chat outputs before they are posted."""

        issues: list[str] = []
        sanitized_text = sanitize_public_chat(agent_output.text or "")
        if agent_output.speak and not sanitized_text:
            issues.append("unsafe_or_empty_public_chat")

        return GuardrailFunctionOutput(
            output_info={
                "issues": issues,
                "speak": bool(agent_output.speak),
                "sanitized_text": sanitized_text,
            },
            tripwire_triggered=bool(issues),
        )


    @output_guardrail(name="clue_chat_intent_output_guardrail")
    def clue_chat_intent_output_guardrail(
        context: RunContextWrapper[SeatAgentContext],
        _agent: Agent[SeatAgentContext],
        agent_output: ChatIntentOutput,
    ) -> GuardrailFunctionOutput:
        """Reject invalid or out-of-scope chat intent plans."""

        issues: list[str] = []
        if agent_output.target_seat_id and not _chat_target_known(context.context, agent_output.target_seat_id):
            issues.append("invalid_target_seat_id")
        if str(agent_output.intent) not in CHAT_INTENTS:
            issues.append("invalid_intent")
        if str(agent_output.tone) not in CHAT_TONES:
            issues.append("invalid_tone")
        if str(agent_output.thread_action) not in CHAT_THREAD_ACTIONS:
            issues.append("invalid_thread_action")
        for delta in list(agent_output.relationship_deltas or []):
            if delta.seat_id and not _chat_target_known(context.context, delta.seat_id):
                issues.append("invalid_relationship_target")
                break
        return GuardrailFunctionOutput(
            output_info={
                "issues": issues,
                "target_seat_id": agent_output.target_seat_id,
                "intent": agent_output.intent,
                "tone": agent_output.tone,
                "thread_action": agent_output.thread_action,
            },
            tripwire_triggered=bool(issues),
        )


    @output_guardrail(name="clue_chat_utterance_output_guardrail")
    def clue_chat_utterance_output_guardrail(
        context: RunContextWrapper[SeatAgentContext],
        _agent: Agent[SeatAgentContext],
        agent_output: ChatUtteranceOutput,
    ) -> GuardrailFunctionOutput:
        """Reject unsafe or obviously fabricated public chat utterances."""

        issues: list[str] = []
        sanitized_text = sanitize_public_chat(agent_output.text or "")
        if not sanitized_text:
            issues.append("unsafe_or_empty_public_chat")
        fabricated_issue = _fabricated_public_fact_issue(context.context, agent_output.text or "")
        if fabricated_issue:
            issues.append(fabricated_issue)
        return GuardrailFunctionOutput(
            output_info={
                "issues": issues,
                "sanitized_text": sanitized_text,
            },
            tripwire_triggered=bool(issues),
        )


    @output_guardrail(name="clue_memory_summary_output_guardrail")
    def clue_memory_summary_output_guardrail(
        context: RunContextWrapper[SeatAgentContext],
        _agent: Agent[SeatAgentContext],
        agent_output: MemorySummaryOutput,
    ) -> GuardrailFunctionOutput:
        """Reject empty or targetless durable memory summaries before persistence."""

        issues: list[str] = []
        if not str(agent_output.first_person_summary or "").strip():
            issues.append("empty_memory_summary")
        for update in list(agent_output.relationship_updates or []):
            if not str(update.target_identity or "").strip():
                issues.append("empty_relationship_target")
                break
        return GuardrailFunctionOutput(
            output_info={
                "issues": issues,
                "relationship_update_count": len(list(agent_output.relationship_updates or [])),
            },
            tripwire_triggered=bool(issues),
        )


def build_agent(runtime_config: LLMRuntimeConfig, *, mode: str = "turn") -> Agent[SeatAgentContext]:
    """Construct the Clue seat agent definition for one turn or chat run.

    The returned agent surface stays deliberately narrow: typed output,
    read-only tools, and output guardrails only. Any new tool or prompt path
    added here should be evaluated against the privacy and legality boundaries
    described in the maintainer docs.
    """

    if not AGENTS_SDK_AVAILABLE:
        raise RuntimeError("OpenAI Agents SDK is not available in this environment.")
    mode_label = str(mode).strip().lower()
    social_tools = [
        get_social_state_summary,
        get_active_chat_threads,
        get_relationship_posture,
        get_recent_public_chat_turns,
        get_recent_public_narrative_turns,
        get_durable_memory_context,
    ]
    memory_tools = [
        get_final_game_context,
        get_durable_memory_context,
        get_social_state_summary,
        get_active_chat_threads,
        get_relationship_posture,
        get_recent_public_chat_turns,
        get_recent_public_narrative_turns,
    ]
    if mode_label == "chat":
        mode_label = "chat_intent"
    if mode_label == "memory_summary":
        return Agent(
            name="Clue Seat Durable Memory Agent",
            instructions=_memory_summary_agent_instructions,
            tools=memory_tools,
            model=runtime_config.model,
            model_settings=ModelSettings(
                tool_choice="required",
                parallel_tool_calls=False,
                max_tokens=620,
                reasoning={"effort": runtime_config.reasoning_effort},
                verbosity="low",
                store=False,
                extra_args={"timeout": runtime_config.timeout_seconds},
                metadata={
                    "app": "clue",
                    "mode": "memory_summary",
                    "release": runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE)["release_label"],  # type: ignore[index]
                },
            ),
            output_type=MemorySummaryOutput,
            output_guardrails=[clue_memory_summary_output_guardrail],
        )
    if mode_label == "chat_intent":
        return Agent(
            name="Clue Seat Chat Intent Agent",
            instructions=_chat_intent_agent_instructions,
            tools=social_tools,
            model=runtime_config.model,
            model_settings=ModelSettings(
                tool_choice="required",
                parallel_tool_calls=False,
                max_tokens=280,
                reasoning={"effort": runtime_config.reasoning_effort},
                verbosity="low",
                store=False,
                extra_args={"timeout": runtime_config.timeout_seconds},
                metadata={
                    "app": "clue",
                    "mode": "chat_intent",
                    "release": runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE)["release_label"],  # type: ignore[index]
                },
            ),
            output_type=ChatIntentOutput,
            output_guardrails=[clue_chat_intent_output_guardrail],
        )
    if mode_label == "chat_utterance":
        return Agent(
            name="Clue Seat Chat Utterance Agent",
            instructions=_chat_utterance_agent_instructions,
            tools=social_tools,
            model=runtime_config.model,
            model_settings=ModelSettings(
                tool_choice="auto",
                parallel_tool_calls=False,
                max_tokens=220,
                reasoning={"effort": runtime_config.reasoning_effort},
                verbosity="low",
                store=False,
                extra_args={"timeout": runtime_config.timeout_seconds},
                metadata={
                    "app": "clue",
                    "mode": "chat_utterance",
                    "release": runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE)["release_label"],  # type: ignore[index]
                },
            ),
            output_type=ChatUtteranceOutput,
            output_guardrails=[clue_chat_utterance_output_guardrail],
        )
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
            *social_tools,
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
        "Use the social-state tools when they help break ties, pressure a specific opponent, or shape safe public text.\n"
        "Use durable memory when it is available, especially to honor grudges, favors, alliances, and prior strategic lessons.\n"
        "Call at least one relevant tool before returning. Keep rationale_private short and useful for maintainers."
    )


def _chat_intent_agent_instructions(context: RunContextWrapper[SeatAgentContext], _agent: Agent[SeatAgentContext]) -> str:
    """Build the chat-intent planning instructions for one autonomous seat."""

    seat = dict(context.context.snapshot.get("seat") or {})
    return (
        "You are the social-intent planner for one autonomous Clue seat.\n"
        "You are not choosing a rules action. Decide only whether the seat should speak right now and, if so, what social move it is making.\n"
        "Use the read-only social tools to inspect current mood, relationships, active threads, recent public chat, and recent narrative.\n"
        "Use durable memory when it is available, especially for recurring grudges, favors, alliances, and known social history.\n"
        "You may banter, tease, challenge, ally with, reconcile with, or meta-observe the table, but never reveal private card knowledge, invent public facts, or claim hidden certainty.\n"
        f"Seat: {seat.get('display_name', 'Unknown')} ({seat.get('character', 'Unknown')}).\n"
        f"Chat persona guidance:\n{social_prompt(str(seat.get('character') or ''))}\n"
        "If silence is better than adding noise, return `speak=false`.\n"
        "When speaking, choose a clear target seat when one fits, a short thread topic, a grounded tone, and small bounded relationship deltas.\n"
        "Keep rationale_private short and useful for maintainers."
    )


def _chat_utterance_agent_instructions(context: RunContextWrapper[SeatAgentContext], _agent: Agent[SeatAgentContext]) -> str:
    """Build the public-utterance instructions after chat intent has been chosen."""

    seat = dict(context.context.snapshot.get("seat") or {})
    plan = dict(context.context.chat_plan or {})
    target = dict(context.context.public_seat_map.get(str(plan.get("target_seat_id") or "")) or {})
    return (
        "You are writing one public table-chat line for an autonomous Clue seat.\n"
        "The social intent is already chosen. Your job is to phrase it naturally, concisely, and in character.\n"
        "You may continue a side discussion, tease, disagree, reconcile, or observe, but never reveal private card knowledge, invent public facts, or claim certainty that the public table has not earned.\n"
        f"Seat: {seat.get('display_name', 'Unknown')} ({seat.get('character', 'Unknown')}).\n"
        f"Chat persona guidance:\n{social_prompt(str(seat.get('character') or ''))}\n"
        f"Chosen intent: {plan.get('intent', '') or 'meta_observe'}.\n"
        f"Target seat: {target.get('display_name', '') or plan.get('target_seat_id', '') or 'none'}.\n"
        f"Topic: {plan.get('topic', '') or 'general table mood'}.\n"
        f"Tone: {plan.get('tone', '') or 'dry'}.\n"
        f"Thread action: {plan.get('thread_action', '') or 'observe'}.\n"
        "Return only the public line plus a short rationale_private/debug_private payload."
    )


def _memory_summary_agent_instructions(context: RunContextWrapper[SeatAgentContext], _agent: Agent[SeatAgentContext]) -> str:
    """Build the completed-game durable-memory instructions for one NHP."""

    seat = dict(context.context.snapshot.get("seat") or {})
    return (
        "You are writing durable first-person memory for one autonomous Clue seat after a completed game.\n"
        "This memory will be loaded into future games for the same canonical character. It is not public table chat.\n"
        "Use the read-only tools to inspect the final game context, visible history, current social state, prior durable memory, and relationships.\n"
        "Write as the character, but keep the summary compact and useful for future strategic and social decisions.\n"
        "Capture what mattered: outcome, useful deductions, strategic mistakes, social pressure, favors, grudges, alliances, betrayals, and future play cues.\n"
        "Relationship updates should target canonical NHP character names or normalized human display-name identities from the provided context.\n"
        f"Seat: {seat.get('display_name', 'Unknown')} ({seat.get('character', 'Unknown')}).\n"
        f"Persona guidance:\n{social_prompt(str(seat.get('character') or ''))}\n"
        "Return a structured durable memory summary and a short rationale_private for maintainers."
    )


def _history_block(events: list[dict[str, Any]], *, empty: str) -> str:
    """Render a compact prompt block from recent public events."""

    if not events:
        return empty
    lines = []
    for event in events:
        lines.append(f"- {str(event.get('message') or '').strip()}")
    return "\n".join(lines)


def build_run_config(context: SeatAgentContext, api_key: str) -> RunConfig:
    """Build the run configuration for one local-first Clue seat-agent turn.

    Trace metadata is intentionally compact and release-aware so cross-run
    debugging stays useful without turning tracing into a second data store.
    """

    if not AGENTS_SDK_AVAILABLE:
        raise RuntimeError("OpenAI Agents SDK is not available in this environment.")
    is_chat = str(context.mode).startswith("chat")
    is_memory = str(context.mode) == "memory_summary"
    return RunConfig(
        model_provider=OpenAIProvider(api_key=api_key, use_responses=True),
        tracing_disabled=not context.runtime_config.tracing_enabled,
        trace_include_sensitive_data=context.runtime_config.trace_include_sensitive_data,
        workflow_name=("Clue Seat Chat" if is_chat else ("Clue Seat Memory" if is_memory else "Clue Seat Decision")),
        trace_id=context.trace_id,
        group_id=context.game_id,
        trace_metadata={
            "app": "clue",
            "game_id": context.game_id,
            "seat_id": context.seat_id,
            "mode": context.mode,
            "release": context.runtime_config.public_summary(sdk_available=AGENTS_SDK_AVAILABLE)["release_label"],  # type: ignore[index]
        },
    )


def build_session(context: SeatAgentContext):
    """Build the encrypted local session used to persist seat-private agent memory."""

    return build_session_for_id(context.session_id, runtime_config=context.runtime_config)


def build_session_for_id(session_id: str, *, runtime_config: LLMRuntimeConfig):
    """Build one encrypted session wrapper for a known session id.

    Session construction is centralized here so turn and chat paths always use
    the same TTL, encryption, and local database rules.
    """

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
    """Convert the raw SDK run result into Clue-facing diagnostics metadata.

    The artifacts payload intentionally mirrors what browser diagnostics and
    tests care about most: trace ids, session ids, tool calls, and guardrail
    outcomes.
    """

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
