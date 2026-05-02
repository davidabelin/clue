"""LLM-seat tests for the v1.9.0 Agents SDK runtime wrapper."""

from __future__ import annotations

import types

import pytest

from clue_agents.llm import LLMDecisionError, LLMSeatAgent, MemorySummaryError
from clue_agents.config import LLMRuntimeConfig
from clue_agents.sdk_runtime import (
    AgentChatOutput,
    AgentTurnOutput,
    MemorySummaryOutput,
    SeatAgentContext,
    _agent_instructions,
    build_run_config,
)
from clue_agents.secrets import _access_secret_version, resolve_openai_api_key, resolve_openai_project_id


def _snapshot(*, legal_actions: dict | None = None) -> dict:
    """Build a minimal seat snapshot for unit-testing the LLM seat wrapper."""

    return {
        "game_id": "game_test",
        "turn_index": 1,
        "seat": {
            "seat_id": "seat_scarlet",
            "display_name": "Miss Scarlet",
            "character": "Miss Scarlet",
            "hand": ["Candlestick"],
        },
        "phase": "start_turn",
        "notebook": {"text": "Watch refutations carefully."},
        "legal_actions": legal_actions or {"available": ["end_turn"]},
        "events": [],
    }


def _artifacts(**overrides) -> dict:
    """Return a compact fake SDK-artifact payload for tests."""

    base = {
        "trace_id": "trace_test",
        "session_id": "game_test:seat_scarlet",
        "last_response_id": "resp_test",
        "tool_calls": [{"name": "get_legal_action_envelope", "arguments": {}}],
        "output_guardrails": [],
        "tool_input_guardrails": [],
    }
    base.update(overrides)
    return base


def test_llm_agent_raises_without_api_key(monkeypatch):
    """Without an API key, the LLM seat should fail loudly instead of faking a move."""

    monkeypatch.delenv("OPENAI_CLUE_SA_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CLUE_SA_KEY_SECRET_VERSION", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_SECRET_VERSION", raising=False)
    agent = LLMSeatAgent(api_key="")
    with pytest.raises(LLMDecisionError) as excinfo:
        agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert excinfo.value.reason == "missing_api_key"
    assert excinfo.value.mode == "turn"


def test_resolve_openai_api_key_prefers_clue_key_over_generic_env(monkeypatch):
    """Clue should use the Clue service-account key, not a generic shared key."""

    monkeypatch.setenv("OPENAI_API_KEY", "generic-zenbot-key")
    monkeypatch.setenv("OPENAI_CLUE_SA_KEY", "clue-service-account-key")

    assert resolve_openai_api_key() == "clue-service-account-key"


def test_resolve_openai_api_key_ignores_generic_env(monkeypatch):
    """Generic OpenAI env vars should not satisfy Clue's LLM credential contract."""

    monkeypatch.setenv("OPENAI_API_KEY", "generic-zenbot-key")
    monkeypatch.setenv("OPENAI_API_KEY_SECRET_VERSION", "projects/aix-labs/secrets/openai-api-key/versions/latest")
    monkeypatch.delenv("OPENAI_CLUE_SA_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CLUE_SA_KEY_SECRET_VERSION", raising=False)

    assert resolve_openai_api_key() == ""


def test_resolve_openai_api_key_reads_clue_secret_manager(monkeypatch):
    """Secret Manager indirection should populate the Clue OpenAI API key when env is blank."""

    class FakeClient:
        """Minimal fake Secret Manager client for unit tests."""

        def access_secret_version(self, request: dict) -> types.SimpleNamespace:
            """Return a canned secret payload for the requested resource name."""

            assert request["name"] == "projects/aix-labs/secrets/clue-openai-api-key/versions/latest"
            return types.SimpleNamespace(payload=types.SimpleNamespace(data=b"secret-from-sm"))

    _access_secret_version.cache_clear()
    monkeypatch.delenv("OPENAI_CLUE_SA_KEY", raising=False)
    monkeypatch.setenv(
        "OPENAI_CLUE_SA_KEY_SECRET_VERSION",
        "projects/aix-labs/secrets/clue-openai-api-key/versions/latest",
    )
    monkeypatch.setattr("clue_agents.secrets._create_secret_manager_client", lambda: FakeClient())

    assert resolve_openai_api_key() == "secret-from-sm"
    _access_secret_version.cache_clear()


def test_resolve_openai_project_id_reads_clue_project_env(monkeypatch):
    """Clue traffic should be explicitly attributed to the configured OpenAI project."""

    monkeypatch.setenv("OPENAI_CLUE_PROJECT_ID", "proj_Lw53USO5NinnThSmUspUs1Kt")

    assert resolve_openai_project_id() == "proj_Lw53USO5NinnThSmUspUs1Kt"


def test_build_run_config_passes_clue_project_to_openai_provider(monkeypatch, tmp_path):
    """The Agents SDK provider should receive the Clue OpenAI project id explicitly."""

    class FakeProvider:
        def __init__(self, **kwargs):
            self.kwargs = dict(kwargs)

    class FakeRunConfig:
        def __init__(self, **kwargs):
            self.kwargs = dict(kwargs)
            self.model_provider = kwargs["model_provider"]

    monkeypatch.setenv("OPENAI_CLUE_PROJECT_ID", "proj_Lw53USO5NinnThSmUspUs1Kt")
    monkeypatch.setattr("clue_agents.sdk_runtime.AGENTS_SDK_AVAILABLE", True)
    monkeypatch.setattr("clue_agents.sdk_runtime.OpenAIProvider", FakeProvider)
    monkeypatch.setattr("clue_agents.sdk_runtime.RunConfig", FakeRunConfig)

    runtime_config = LLMRuntimeConfig(
        model="gpt-test",
        reasoning_effort="medium",
        timeout_seconds=12.0,
        max_tool_calls=6,
        max_turns=8,
        tracing_enabled=False,
        trace_include_sensitive_data=False,
        session_ttl_seconds=900,
        session_db_path=str(tmp_path / "sessions.db"),
        session_encryption_key="test-session-key",
        eval_export_enabled=False,
    )
    context = SeatAgentContext(
        runtime_config=runtime_config,
        snapshot=_snapshot(),
        tool_snapshot={},
        accusation_gate={"ready": False},
        trace_id="trace_test",
        session_id="game_test:seat_scarlet",
    )

    run_config = build_run_config(context, "clue-service-account-key")

    assert run_config.model_provider.kwargs["api_key"] == "clue-service-account-key"
    assert run_config.model_provider.kwargs["project"] == "proj_Lw53USO5NinnThSmUspUs1Kt"
    assert run_config.model_provider.kwargs["use_responses"] is True


def test_turn_prompt_prioritizes_prompt_action_over_social_writes():
    """Turn decisions should not pressure verbose personas into durable social writes."""

    class FakeContext:
        snapshot = _snapshot()
        legal_actions = {"available": ["move"], "current_room": "", "move_targets": [{"node_id": "hall"}]}
        notebook_text = "Watch refutations carefully."
        accusation_gate = {"ready": False}

    class FakeWrapper:
        context = FakeContext()

    instructions = _agent_instructions(FakeWrapper(), None)

    assert "Use write tools" not in instructions
    assert "Return promptly" in instructions
    assert "rationale_private under 160 characters" in instructions


def test_llm_agent_raises_on_model_error(monkeypatch):
    """Unexpected runner failures should not trigger a deterministic fake move."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    agent = LLMSeatAgent(api_key="test-key")
    with pytest.raises(LLMDecisionError) as excinfo:
        agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert excinfo.value.reason == "model_error"


def test_llm_agent_rejects_illegal_actions(monkeypatch):
    """Illegal model actions should fail the LLM decision instead of using heuristics."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            AgentTurnOutput(action="teleport", rationale_private="illegal"),
            _artifacts(),
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    with pytest.raises(LLMDecisionError) as excinfo:
        agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert excinfo.value.reason == "illegal_action"


def test_llm_agent_raises_on_timeout(monkeypatch):
    """Timeouts from the agent runner should fail loudly without a heuristic move."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (_ for _ in ()).throw(TimeoutError("timed out")),
    )

    agent = LLMSeatAgent(api_key="test-key")
    with pytest.raises(LLMDecisionError) as excinfo:
        agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert excinfo.value.reason == "timeout"


def test_llm_agent_blocks_unsafe_public_leak(monkeypatch):
    """Unsafe public ownership claims should fail the LLM turn."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            AgentTurnOutput(
                action="end_turn",
                text="Colonel Mustard has the Rope.",
                rationale_private="done",
            ),
            _artifacts(),
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    with pytest.raises(LLMDecisionError) as excinfo:
        agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert excinfo.value.reason == "unsafe_public_chat"


def test_llm_agent_uses_secret_manager_key_when_env_var_missing(monkeypatch):
    """The seat wrapper should cache the key resolved through Secret Manager."""

    monkeypatch.setattr(
        "clue_agents.llm.resolve_openai_api_key",
        lambda *, api_key="": "secret-from-sm",
    )

    agent = LLMSeatAgent(api_key="")
    assert agent._api_key == "secret-from-sm"


def test_llm_agent_records_sdk_metadata_on_success(monkeypatch):
    """Successful runs should surface model, trace, session, and tool metadata."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            AgentTurnOutput(action="end_turn", rationale_private="done"),
            _artifacts(
                trace_id="trace_123",
                session_id="game_test:seat_scarlet",
                tool_calls=[
                    {"name": "get_legal_action_envelope", "arguments": {}},
                    {"name": "get_belief_summary", "arguments": {}},
                ],
            ),
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={"belief_summary": {"joint_case_entropy_bits": 1.2}})
    assert decision.action == "end_turn"
    assert decision.agent_meta["backend"] == "openai_agents_sdk"
    assert decision.agent_meta["trace_id"] == "trace_123"
    assert decision.agent_meta["tool_call_count"] == 2
    assert decision.debug_private["sdk_trace_id"] == "trace_123"


def test_llm_agent_passes_write_sink_and_surfaces_tool_writes(monkeypatch):
    """SDK context writes should be returned in private diagnostics and agent metadata."""

    sink = object()

    def _run(self, context):
        assert context.write_sink is sink
        context.record_tool_write("record_memory_note", {"status": "ok", "note": {"id": "note_test"}})
        return (
            AgentTurnOutput(action="end_turn", rationale_private="done"),
            _artifacts(tool_writes=list(context.tool_write_log)),
        )

    monkeypatch.setattr(LLMSeatAgent, "_run_agent", _run)

    agent = LLMSeatAgent(api_key="test-key", write_sink=sink)
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})

    assert decision.agent_meta["tool_writes"][0]["name"] == "record_memory_note"
    assert decision.debug_private["sdk_tool_writes"][0]["result"]["status"] == "ok"


def test_llm_agent_keeps_tool_write_diagnostics_when_final_output_fails(monkeypatch):
    """Immediate writes should remain audit-visible when a later model output fails."""

    class FakeSink:
        def __init__(self):
            self.rows = []

        def record_note(self, **kwargs):
            self.rows.append(dict(kwargs))
            return {"status": "ok", "note": {"id": "note_failure"}}

    sink = FakeSink()

    def _run(self, context):
        result = context.write_sink.record_note(
            game_id=context.game_id,
            seat_id=context.seat_id,
            note_kind="memory_note",
            note_text="This should survive final output failure.",
            tool_name="record_memory_note",
        )
        context.record_tool_write("record_memory_note", result)
        raise RuntimeError("bad final output")

    monkeypatch.setattr(LLMSeatAgent, "_run_agent", _run)

    agent = LLMSeatAgent(api_key="test-key", write_sink=sink)
    with pytest.raises(LLMDecisionError) as excinfo:
        agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})

    assert sink.rows[0]["note_text"].startswith("This should survive")
    assert excinfo.value.reason == "model_error"
    assert excinfo.value.debug["llm_debug"]["tool_writes"][0]["result"]["note"]["id"] == "note_failure"


def test_llm_agent_holds_early_accusation_without_heuristic_fallback(monkeypatch):
    """Even legal model accusations should fail when the pacing gate is not ready."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            AgentTurnOutput(
                action="accuse",
                suspect="Colonel Mustard",
                weapon="Knife",
                room="Hall",
                rationale_private="I am sure enough.",
            ),
            _artifacts(),
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    with pytest.raises(LLMDecisionError) as excinfo:
        agent.decide_turn(
            snapshot=_snapshot(legal_actions={"available": ["accuse", "end_turn"]}),
            tool_snapshot={
                "accusation": {
                    "should_accuse": True,
                    "confidence": 0.87,
                    "confidence_gap": 0.2,
                    "entropy_bits": 0.72,
                    "sample_count": 64,
                    "accusation": {"suspect": "Colonel Mustard", "weapon": "Knife", "room": "Hall"},
                },
                "belief_summary": {
                    "joint_case_entropy_bits": 0.72,
                    "resolved_cards": 5,
                    "case_file_candidate_counts": {"suspect": 2, "weapon": 2, "room": 2},
                },
            },
        )

    assert excinfo.value.reason == "accusation_hold"


def test_llm_agent_clear_session_uses_local_session_store(monkeypatch):
    """Session cleanup should target the local encrypted session wrapper."""

    called: dict[str, list[str] | str] = {"session_ids": []}

    class FakeSession:
        """Small async session stub that records the cleared session id."""

        async def clear_session(self) -> None:
            called["cleared"] = "yes"

    def _fake_build_session(session_id: str, runtime_config):
        """Capture the requested session id and return the fake session wrapper."""

        called["session_ids"].append(session_id)
        return FakeSession()

    monkeypatch.setattr(
        "clue_agents.llm.build_session_for_id",
        _fake_build_session,
    )

    agent = LLMSeatAgent(api_key="test-key")
    monkeypatch.setattr(
        "asyncio.run",
        lambda coro: (coro.close(), called.setdefault("ran", "yes"))[-1],
    )
    agent.clear_session(game_id="game_test", seat_id="seat_scarlet")

    assert called["session_ids"] == ["game_test:seat_scarlet", "game_test:seat_scarlet:chat", "game_test:seat_scarlet:memory"]


def test_llm_agent_chat_uses_separate_session_metadata(monkeypatch):
    """Idle-chat runs should use one compact call and surface chat session metadata."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            AgentChatOutput(speak=True, text="We are all showing far too much.", rationale_private="chat"),
            _artifacts(session_id="game_test:seat_scarlet:chat", trace_id="trace_chat"),
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_chat(snapshot=_snapshot())

    assert decision.speak is True
    assert decision.text == "We are all showing far too much."
    assert decision.agent_meta["session_id"] == "game_test:seat_scarlet:chat"
    assert decision.debug_private["sdk_session_id"] == "game_test:seat_scarlet:chat"


def test_llm_agent_chat_raises_without_api_key(monkeypatch):
    """Idle chat should not use heuristic chat text when OpenAI credentials are absent."""

    monkeypatch.delenv("OPENAI_CLUE_SA_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CLUE_SA_KEY_SECRET_VERSION", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_SECRET_VERSION", raising=False)
    agent = LLMSeatAgent(api_key="")

    with pytest.raises(LLMDecisionError) as excinfo:
        agent.decide_chat(snapshot=_snapshot())

    assert excinfo.value.reason == "missing_api_key"
    assert excinfo.value.mode == "chat"


def test_llm_agent_chat_drops_unsafe_public_leak(monkeypatch):
    """Unsafe chat-only outputs should be suppressed instead of posted."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            AgentChatOutput(speak=True, text="Colonel Mustard has the Rope.", rationale_private="chat"),
            _artifacts(),
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_chat(snapshot=_snapshot())

    assert decision.speak is False
    assert decision.text == ""
    assert decision.agent_meta["fallback_reason"] == "unsafe_public_chat"


def test_llm_agent_chat_preserves_model_silence(monkeypatch):
    """A deliberate compact-chat no-speak output should remain a non-error silence."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            AgentChatOutput(speak=False, text="", rationale_private="Nothing useful to add."),
            _artifacts(session_id="game_test:seat_scarlet:chat"),
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_chat(snapshot=_snapshot())

    assert decision.speak is False
    assert decision.text == ""
    assert decision.agent_meta["fallback_reason"] == "model_chose_silence"
    assert decision.agent_meta["session_id"] == "game_test:seat_scarlet:chat"


def test_llm_agent_chat_malformed_output_fails_loudly(monkeypatch):
    """Malformed compact-chat model output should be visible as an LLM chat failure."""

    def _raise_model_error(self, context):
        raise RuntimeError("Pydantic EOF while parsing AgentChatOutput")

    monkeypatch.setattr(LLMSeatAgent, "_run_agent", _raise_model_error)

    agent = LLMSeatAgent(api_key="test-key")
    with pytest.raises(LLMDecisionError) as excinfo:
        agent.decide_chat(snapshot=_snapshot())

    assert excinfo.value.reason == "model_error"
    assert excinfo.value.mode == "chat"
    assert "AgentChatOutput" in str(excinfo.value.error)


def test_llm_agent_memory_summary_records_metadata(monkeypatch):
    """Successful memory-summary runs should return durable summary data and SDK metadata."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            MemorySummaryOutput(
                first_person_summary="I remember how the Colonel hesitated.",
                strategic_lessons=["Push earlier."],
                social_observations=["Mustard dislikes hesitation."],
                relationship_updates=[
                    {
                        "target_kind": "nhp",
                        "target_identity": "Colonel Mustard",
                        "affinity_delta": -1,
                        "trust_delta": -1,
                        "friction_delta": 1,
                        "note": "He looked too certain.",
                    }
                ],
                rationale_private="memory",
            ),
            _artifacts(session_id="game_test:seat_scarlet:memory", trace_id="trace_memory"),
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.summarize_memory(snapshot=_snapshot())

    assert decision.summary["first_person_summary"].startswith("I remember")
    assert decision.relationship_updates[0]["target_identity"] == "Colonel Mustard"
    assert decision.agent_meta["session_id"] == "game_test:seat_scarlet:memory"
    assert decision.debug_private["sdk_trace_id"] == "trace_memory"


def test_llm_agent_memory_summary_has_no_missing_api_fallback(monkeypatch):
    """The durable memory path should raise instead of using heuristic prose fallback."""

    monkeypatch.delenv("OPENAI_CLUE_SA_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CLUE_SA_KEY_SECRET_VERSION", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_SECRET_VERSION", raising=False)
    agent = LLMSeatAgent(api_key="")

    with pytest.raises(MemorySummaryError) as excinfo:
        agent.summarize_memory(snapshot=_snapshot())

    assert excinfo.value.reason == "missing_api_key"


def test_llm_agent_rejects_empty_memory_summary(monkeypatch):
    """Malformed memory output should fail the job cleanly for runtime queuing."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            MemorySummaryOutput(first_person_summary="", rationale_private="bad"),
            _artifacts(session_id="game_test:seat_scarlet:memory"),
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    with pytest.raises(MemorySummaryError) as excinfo:
        agent.summarize_memory(snapshot=_snapshot())

    assert excinfo.value.reason == "invalid_memory_summary"
