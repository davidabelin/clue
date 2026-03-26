from __future__ import annotations

import types

from clue_agents.llm import LLMSeatAgent


def _snapshot(*, legal_actions: dict | None = None) -> dict:
    return {
        "seat": {
            "display_name": "Miss Scarlet",
            "character": "Miss Scarlet",
            "hand": ["Candlestick"],
        },
        "phase": "start_turn",
        "legal_actions": legal_actions or {"available": ["end_turn"]},
    }


def test_llm_agent_falls_back_without_api_key():
    agent = LLMSeatAgent(api_key="")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert decision.action == "end_turn"


def test_llm_agent_falls_back_on_malformed_json(monkeypatch):
    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.responses = types.SimpleNamespace(
                create=lambda **kwargs: types.SimpleNamespace(output_text="{not-json")
            )

    monkeypatch.setattr("clue_agents.llm.OpenAI", FakeClient)

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert decision.action == "end_turn"


def test_llm_agent_rejects_illegal_actions(monkeypatch):
    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.responses = types.SimpleNamespace(
                create=lambda **kwargs: types.SimpleNamespace(
                    output_text='{"action":"teleport","rationale_private":"illegal"}'
                )
            )

    monkeypatch.setattr("clue_agents.llm.OpenAI", FakeClient)

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert decision.action == "end_turn"


def test_llm_agent_falls_back_on_timeout(monkeypatch):
    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.responses = types.SimpleNamespace(
                create=lambda **kwargs: (_ for _ in ()).throw(TimeoutError("timed out"))
            )

    monkeypatch.setattr("clue_agents.llm.OpenAI", FakeClient)

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert decision.action == "end_turn"


def test_llm_agent_sanitizes_public_leak(monkeypatch):
    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.responses = types.SimpleNamespace(
                create=lambda **kwargs: types.SimpleNamespace(
                    output_text=(
                        '{"action":"end_turn","text":"Colonel Mustard has the Rope.",'
                        '"rationale_private":"done"}'
                    )
                )
            )

    monkeypatch.setattr("clue_agents.llm.OpenAI", FakeClient)

    agent = LLMSeatAgent(api_key="test-key")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert decision.action == "end_turn"
    assert decision.text == ""
