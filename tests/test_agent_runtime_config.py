"""Configuration and guardrail tests for the v1.7.3 seat runtime."""

from __future__ import annotations

import types

from clue_agents.config import load_llm_runtime_config
from clue_agents.sdk_runtime import (
    ChatIntentOutput,
    build_seat_context,
    clue_chat_intent_output_guardrail,
    move_target_scope_guardrail,
    refute_card_scope_guardrail,
)


def _snapshot(*, legal_actions: dict | None = None) -> dict:
    """Build a small snapshot suitable for config and guardrail tests."""

    return {
        "game_id": "game_cfg",
        "turn_index": 2,
        "seat": {
            "seat_id": "seat_scarlet",
            "display_name": "Miss Scarlet",
            "character": "Miss Scarlet",
            "position": "study",
            "hand": ["Candlestick", "Rope"],
        },
        "phase": "move",
        "notebook": {"text": "Private note"},
        "seats": [
            {
                "seat_id": "seat_mustard",
                "display_name": "Colonel Mustard",
                "character": "Colonel Mustard",
                "seat_kind": "llm",
            }
        ],
        "legal_actions": legal_actions
        or {
            "available": ["move", "show_refute_card", "end_turn"],
            "move_targets": [{"node_id": "study_hall", "label": "Study / Hall", "cost": 1, "mode": "walk"}],
            "refute_cards": ["Candlestick"],
        },
        "events": [],
    }


def test_llm_runtime_config_defaults(monkeypatch, tmp_path):
    """The normalized runtime config should choose privacy-first defaults."""

    monkeypatch.setenv("CLUE_AGENT_SESSION_DB_PATH", str(tmp_path / "agent_sessions.db"))
    load_llm_runtime_config.cache_clear()

    config = load_llm_runtime_config()

    assert config.model == "gpt-5.4-mini-2026-03-17"
    assert config.reasoning_effort == "medium"
    assert config.tracing_enabled is False
    assert config.trace_include_sensitive_data is False
    assert config.max_tool_calls == 6
    assert config.session_db_path.endswith("agent_sessions.db")


def test_llm_runtime_config_respects_env_overrides(monkeypatch, tmp_path):
    """Environment overrides should flow into the normalized config object."""

    monkeypatch.setenv("CLUE_LLM_MODEL", "gpt-5.4")
    monkeypatch.setenv("CLUE_LLM_REASONING_EFFORT", "high")
    monkeypatch.setenv("CLUE_AGENT_TRACING_ENABLED", "1")
    monkeypatch.setenv("CLUE_AGENT_TRACE_INCLUDE_SENSITIVE_DATA", "1")
    monkeypatch.setenv("CLUE_LLM_MAX_TOOL_CALLS", "9")
    monkeypatch.setenv("CLUE_AGENT_SESSION_DB_PATH", str(tmp_path / "custom_sessions.db"))
    load_llm_runtime_config.cache_clear()

    config = load_llm_runtime_config()

    assert config.model == "gpt-5.4"
    assert config.reasoning_effort == "high"
    assert config.tracing_enabled is True
    assert config.trace_include_sensitive_data is True
    assert config.max_tool_calls == 9
    assert config.session_db_path.endswith("custom_sessions.db")


def test_move_target_tool_guardrail_rejects_out_of_scope_target(monkeypatch, tmp_path):
    """Move-target inspection should reject arguments outside the current legal set."""

    monkeypatch.setenv("CLUE_AGENT_SESSION_DB_PATH", str(tmp_path / "agent_sessions.db"))
    load_llm_runtime_config.cache_clear()
    context = build_seat_context(
        snapshot=_snapshot(),
        tool_snapshot={},
        accusation_gate={"ready": False},
        runtime_config=load_llm_runtime_config(),
    )
    data = types.SimpleNamespace(
        context=types.SimpleNamespace(
            context=context,
            tool_arguments='{"target_node":"kitchen"}',
        )
    )

    result = move_target_scope_guardrail.guardrail_function(data)

    assert result.behavior["type"] == "reject_content"
    assert "study_hall" in result.output_info["allowed"]


def test_refute_card_tool_guardrail_allows_current_private_card(monkeypatch, tmp_path):
    """Refute-card inspection should allow cards from the current legal refute set."""

    monkeypatch.setenv("CLUE_AGENT_SESSION_DB_PATH", str(tmp_path / "agent_sessions.db"))
    load_llm_runtime_config.cache_clear()
    context = build_seat_context(
        snapshot=_snapshot(),
        tool_snapshot={},
        accusation_gate={"ready": False},
        runtime_config=load_llm_runtime_config(),
    )
    data = types.SimpleNamespace(
        context=types.SimpleNamespace(
            context=context,
            tool_arguments='{"card":"Candlestick"}',
        )
    )

    result = refute_card_scope_guardrail.guardrail_function(data)

    assert result.behavior["type"] == "allow"


def test_chat_context_uses_separate_session_id(monkeypatch, tmp_path):
    """Chat-mode seat contexts should keep a separate local session namespace."""

    monkeypatch.setenv("CLUE_AGENT_SESSION_DB_PATH", str(tmp_path / "agent_sessions.db"))
    load_llm_runtime_config.cache_clear()
    context = build_seat_context(
        snapshot=_snapshot(),
        tool_snapshot={},
        accusation_gate={"ready": False},
        runtime_config=load_llm_runtime_config(),
        mode="chat",
    )

    assert context.mode == "chat"
    assert context.session_id == "game_cfg:seat_scarlet:chat"


def test_chat_intent_guardrail_rejects_unknown_target_seat(monkeypatch, tmp_path):
    """Chat-intent guardrails should block target seat ids outside the visible seat map."""

    monkeypatch.setenv("CLUE_AGENT_SESSION_DB_PATH", str(tmp_path / "agent_sessions.db"))
    load_llm_runtime_config.cache_clear()
    context = build_seat_context(
        snapshot=_snapshot(),
        tool_snapshot={},
        accusation_gate={"ready": False},
        runtime_config=load_llm_runtime_config(),
        mode="chat_intent",
    )

    result = clue_chat_intent_output_guardrail.guardrail_function(
        types.SimpleNamespace(context=context),
        None,
        ChatIntentOutput(
            speak=True,
            intent="challenge",
            target_seat_id="seat_not_real",
            topic="phantom",
            tone="cutting",
            thread_action="open",
        ),
    )

    assert result.tripwire_triggered is True
    assert "invalid_target_seat_id" in result.output_info["issues"]
