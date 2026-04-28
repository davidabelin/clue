"""Shared pytest fixtures for the standalone Clue app tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clue_web import create_app
from clue_agents.config import load_llm_runtime_config
from clue_agents.profile_loader import clear_profile_caches


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    """Create one isolated app instance backed by a temporary SQLite database."""

    monkeypatch.setenv("CLUE_AGENT_SESSION_DB_PATH", str(tmp_path / "clue_agent_sessions.db"))
    monkeypatch.setenv("CLUE_AGENT_SESSION_ENCRYPTION_KEY", "test-session-key")
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "CLUE_SECRET_KEY": "test-secret-key",
            "DB_PATH": str(tmp_path / "clue.db"),
        }
    )
    yield app


@pytest.fixture(autouse=True)
def isolate_openai_env(monkeypatch):
    """Clear live OpenAI env vars so tests do not accidentally call the network."""

    load_llm_runtime_config.cache_clear()
    clear_profile_caches()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_SECRET_VERSION", raising=False)
    monkeypatch.delenv("CLUE_AGENT_SESSION_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("CLUE_AGENT_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("CLUE_AGENT_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("CLUE_AGENT_TRACE_INCLUDE_SENSITIVE_DATA", raising=False)
    monkeypatch.delenv("CLUE_AGENT_EVAL_EXPORT_ENABLED", raising=False)
    monkeypatch.delenv("CLUE_SECRET_KEY_SECRET", raising=False)
    monkeypatch.delenv("CLUE_ADMIN_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("CLUE_IDLE_CHAT_ENABLED", raising=False)
    monkeypatch.delenv("CLUE_PROACTIVE_CHAT_ENABLED", raising=False)
    monkeypatch.delenv("CLUE_PROACTIVE_CHAT_CHANCE_MULTIPLIER", raising=False)
    yield
    clear_profile_caches()
    load_llm_runtime_config.cache_clear()


@pytest.fixture
def client(app):
    """Return the Flask test client for the configured Clue app."""

    return app.test_client()
