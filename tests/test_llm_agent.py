from __future__ import annotations

import types

from clue_agents.llm import LLMSeatAgent
from clue_agents.secrets import _access_secret_version, resolve_openai_api_key


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


def test_resolve_openai_api_key_reads_secret_manager(monkeypatch):
    class FakeClient:
        def access_secret_version(self, request: dict) -> types.SimpleNamespace:
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


def test_llm_agent_uses_secret_manager_key_when_env_var_missing(monkeypatch):
    captured: dict[str, str] = {}

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key
            self.responses = types.SimpleNamespace(
                create=lambda **kwargs: types.SimpleNamespace(
                    output_text='{"action":"end_turn","rationale_private":"done"}'
                )
            )

    monkeypatch.setattr("clue_agents.llm.OpenAI", FakeClient)
    monkeypatch.setattr(
        "clue_agents.llm.resolve_openai_api_key",
        lambda *, api_key="": "secret-from-sm",
    )

    agent = LLMSeatAgent(api_key="")
    decision = agent.decide_turn(snapshot=_snapshot(), tool_snapshot={})
    assert captured["api_key"] == "secret-from-sm"
    assert decision.action == "end_turn"
