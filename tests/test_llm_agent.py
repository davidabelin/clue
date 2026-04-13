"""LLM-seat tests for the v1.7.0 Agents SDK runtime wrapper."""

from __future__ import annotations

import types

from clue_agents.llm import LLMSeatAgent
from clue_agents.sdk_runtime import AgentChatOutput, AgentTurnOutput, ChatIntentOutput, ChatUtteranceOutput
from clue_agents.secrets import _access_secret_version, resolve_openai_api_key


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


def test_llm_agent_falls_back_without_api_key(monkeypatch):
    """Without an API key, the LLM seat should defer to the heuristic fallback."""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_SECRET_VERSION", raising=False)
    agent = LLMSeatAgent(api_key="")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert decision.action == "end_turn"
    assert decision.agent_meta["fallback_reason"] == "missing_api_key"


def test_resolve_openai_api_key_reads_secret_manager(monkeypatch):
    """Secret Manager indirection should populate the OpenAI API key when env is blank."""

    class FakeClient:
        """Minimal fake Secret Manager client for unit tests."""

        def access_secret_version(self, request: dict) -> types.SimpleNamespace:
            """Return a canned secret payload for the requested resource name."""

            assert request["name"] == "projects/aix-labs/secrets/openai-api-key/versions/latest"
            return types.SimpleNamespace(payload=types.SimpleNamespace(data=b"secret-from-sm"))

    _access_secret_version.cache_clear()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(
        "OPENAI_API_KEY_SECRET_VERSION",
        "projects/aix-labs/secrets/openai-api-key/versions/latest",
    )
    monkeypatch.setattr("clue_agents.secrets._create_secret_manager_client", lambda: FakeClient())

    assert resolve_openai_api_key() == "secret-from-sm"
    _access_secret_version.cache_clear()


def test_llm_agent_falls_back_on_model_error(monkeypatch):
    """Unexpected runner failures should trigger the deterministic fallback policy."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert decision.action == "end_turn"
    assert decision.agent_meta["fallback_reason"] == "model_error"


def test_llm_agent_rejects_illegal_actions(monkeypatch):
    """Illegal model actions should be rejected in favor of the heuristic fallback."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            AgentTurnOutput(action="teleport", rationale_private="illegal"),
            _artifacts(),
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert decision.action == "end_turn"
    assert decision.agent_meta["fallback_reason"] == "illegal_action"


def test_llm_agent_falls_back_on_timeout(monkeypatch):
    """Timeouts from the agent runner should fall back without crashing the turn."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (_ for _ in ()).throw(TimeoutError("timed out")),
    )

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert decision.action == "end_turn"
    assert decision.agent_meta["fallback_reason"] == "timeout"


def test_llm_agent_blocks_unsafe_public_leak(monkeypatch):
    """Unsafe public ownership claims should cause the model path to fall back."""

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
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert decision.action == "end_turn"
    assert decision.agent_meta["fallback_reason"] == "unsafe_public_chat"


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


def test_llm_agent_holds_early_accusation_and_uses_safe_fallback(monkeypatch):
    """Even legal model accusations should be deferred when the pacing gate is not ready."""

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
    decision = agent.decide_turn(
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

    assert decision.action == "end_turn"
    assert decision.agent_meta["fallback_reason"] == "accusation_hold"


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

    assert called["session_ids"] == ["game_test:seat_scarlet", "game_test:seat_scarlet:chat"]


def test_llm_agent_chat_uses_separate_session_metadata(monkeypatch):
    """Idle-chat runs should surface the dedicated chat session id and intent metadata."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            (
                ChatIntentOutput(
                    speak=True,
                    intent="challenge",
                    target_seat_id="seat_mustard",
                    topic="mustard_reaction",
                    tone="cutting",
                    thread_action="continue",
                    rationale_private="Press the colonel.",
                ),
                _artifacts(session_id="game_test:seat_scarlet:chat", trace_id="trace_chat_intent"),
            )
            if context.mode == "chat_intent"
            else (
                ChatUtteranceOutput(text="We are all showing far too much.", rationale_private="chat"),
                _artifacts(session_id="game_test:seat_scarlet:chat", trace_id="trace_chat_utterance"),
            )
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_chat(snapshot=_snapshot())

    assert decision.speak is True
    assert decision.text == "We are all showing far too much."
    assert decision.intent == "challenge"
    assert decision.target_seat_id == "seat_mustard"
    assert decision.agent_meta["session_id"] == "game_test:seat_scarlet:chat"
    assert decision.debug_private["sdk_session_id"] == "game_test:seat_scarlet:chat"


def test_llm_agent_chat_drops_unsafe_public_leak(monkeypatch):
    """Unsafe chat-only outputs should be suppressed instead of posted."""

    monkeypatch.setattr(
        LLMSeatAgent,
        "_run_agent",
        lambda self, context: (
            (
                ChatIntentOutput(
                    speak=True,
                    intent="challenge",
                    target_seat_id="seat_mustard",
                    topic="mustard_cards",
                    tone="cutting",
                    thread_action="open",
                    rationale_private="push him",
                ),
                _artifacts(),
            )
            if context.mode == "chat_intent"
            else (
                ChatUtteranceOutput(text="Colonel Mustard has the Rope.", rationale_private="chat"),
                _artifacts(),
            )
        ),
    )

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_chat(snapshot=_snapshot())

    assert decision.speak is False
    assert decision.text == ""
    assert decision.agent_meta["fallback_reason"] == "unsafe_public_chat"
