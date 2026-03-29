"""Runtime configuration helpers for OpenAI-backed Clue seat agents.

This module centralizes the environment contract for the v1.5.0 seat runtime.
The rest of the codebase should read one normalized config object rather than
repeating environment parsing logic or making ad hoc decisions about privacy,
latency, tracing, and model defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path

from clue_core.version import CLUE_RELEASE_LABEL, CLUE_VERSION


_VALID_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}


def _env_bool(name: str, default: bool) -> bool:
    """Parse one boolean-like environment variable with a conservative fallback."""

    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int) -> int:
    """Parse one integer environment variable while enforcing a lower bound."""

    try:
        return max(int(str(os.getenv(name, default)).strip() or default), minimum)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, *, minimum: float) -> float:
    """Parse one float environment variable while enforcing a lower bound."""

    try:
        return max(float(str(os.getenv(name, default)).strip() or default), minimum)
    except (TypeError, ValueError):
        return default


def _default_session_db_path() -> Path:
    """Return the repo-local SQLite file used for encrypted seat-agent sessions."""

    root = Path(__file__).resolve().parents[1]
    return root / "data" / "clue_agent_sessions.db"


@dataclass(slots=True)
class LLMRuntimeConfig:
    """Normalized configuration for one OpenAI Agents SDK seat runtime.

    The config deliberately separates:
    - public diagnostics fields that are safe to expose broadly
    - private operational fields such as encryption keys and full session paths

    This keeps the analysis payload useful without accidentally making the
    diagnostics layer a new leak surface.
    """

    model: str
    reasoning_effort: str
    timeout_seconds: float
    max_tool_calls: int
    max_turns: int
    tracing_enabled: bool
    trace_include_sensitive_data: bool
    session_ttl_seconds: int
    session_db_path: str
    session_encryption_key: str
    eval_export_enabled: bool

    @property
    def session_db_url(self) -> str:
        """Return the async SQLAlchemy URL used by the Agents SDK session store."""

        path = Path(self.session_db_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path.as_posix()}"

    def with_model_override(self, model: str) -> "LLMRuntimeConfig":
        """Return a copy whose model snapshot respects a per-seat override."""

        override = str(model or "").strip()
        if not override:
            return self
        return LLMRuntimeConfig(
            model=override,
            reasoning_effort=self.reasoning_effort,
            timeout_seconds=self.timeout_seconds,
            max_tool_calls=self.max_tool_calls,
            max_turns=self.max_turns,
            tracing_enabled=self.tracing_enabled,
            trace_include_sensitive_data=self.trace_include_sensitive_data,
            session_ttl_seconds=self.session_ttl_seconds,
            session_db_path=self.session_db_path,
            session_encryption_key=self.session_encryption_key,
            eval_export_enabled=self.eval_export_enabled,
        )

    def public_summary(self, *, sdk_available: bool) -> dict[str, object]:
        """Build the safe diagnostics payload surfaced to maintainers and tests."""

        return {
            "release_label": CLUE_RELEASE_LABEL,
            "version": CLUE_VERSION,
            "sdk_backend": "openai_agents_sdk",
            "sdk_available": bool(sdk_available),
            "default_model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "timeout_seconds": self.timeout_seconds,
            "max_tool_calls": self.max_tool_calls,
            "max_turns": self.max_turns,
            "tracing_enabled": self.tracing_enabled,
            "trace_include_sensitive_data": self.trace_include_sensitive_data,
            "session_ttl_seconds": self.session_ttl_seconds,
            "session_store": "local_encrypted_sqlalchemy_sqlite",
            "eval_export_enabled": self.eval_export_enabled,
        }


@lru_cache(maxsize=1)
def load_llm_runtime_config() -> LLMRuntimeConfig:
    """Load and cache the normalized v1.5.0 LLM runtime configuration."""

    reasoning_effort = str(os.getenv("CLUE_LLM_REASONING_EFFORT", "medium")).strip().lower()
    if reasoning_effort not in _VALID_REASONING_EFFORTS:
        reasoning_effort = "medium"
    max_tool_calls = _env_int("CLUE_LLM_MAX_TOOL_CALLS", 6, minimum=1)
    max_turns = _env_int("CLUE_AGENT_MAX_TURNS", max_tool_calls + 2, minimum=max_tool_calls + 1)
    session_db_path = str(os.getenv("CLUE_AGENT_SESSION_DB_PATH", "")).strip() or str(_default_session_db_path())
    session_key = (
        str(os.getenv("CLUE_AGENT_SESSION_ENCRYPTION_KEY", "")).strip()
        or str(os.getenv("CLUE_SECRET_KEY", "")).strip()
        or "clue-dev-session-key"
    )
    return LLMRuntimeConfig(
        model=str(os.getenv("CLUE_LLM_MODEL", "gpt-5.4-mini-2026-03-17")).strip() or "gpt-5.4-mini-2026-03-17",
        reasoning_effort=reasoning_effort,
        timeout_seconds=_env_float("CLUE_LLM_TIMEOUT_SECONDS", 12.0, minimum=1.0),
        max_tool_calls=max_tool_calls,
        max_turns=max_turns,
        tracing_enabled=_env_bool("CLUE_AGENT_TRACING_ENABLED", False),
        trace_include_sensitive_data=_env_bool("CLUE_AGENT_TRACE_INCLUDE_SENSITIVE_DATA", False),
        session_ttl_seconds=_env_int("CLUE_AGENT_SESSION_TTL_SECONDS", 900, minimum=60),
        session_db_path=session_db_path,
        session_encryption_key=session_key,
        eval_export_enabled=_env_bool("CLUE_AGENT_EVAL_EXPORT_ENABLED", False),
    )
