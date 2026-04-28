"""Standalone Clue runtime service: create games, resolve tokens, and auto-run seats.

This module is the orchestration layer above the pure rules engine. It is where
storage, filtered snapshots, deduction summaries, telemetry, social memory, and
seat-agent execution are stitched together. Future changes that touch privacy,
LLM failure behavior, or request-to-action flow almost always pass through here.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import os
import secrets
import time
from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer

from clue_agents import AgentRuntime
from clue_agents.llm import LLMDecisionError, MemorySummaryError
from clue_agents.profile_loader import (
    assign_chat_model_profiles,
    assign_model_profiles,
    persona_chattiness,
    persona_relationship_map,
)
from clue_agents.safety import sanitize_public_chat
from clue_core.deduction import build_tool_snapshot
from clue_core.engine import GameMaster, build_filtered_snapshot
from clue_core.events import make_event
from clue_core.setup import build_hidden_setup, build_initial_state
from clue_core.types import DEFAULT_UI_MODE, LIVE_UI_MODES, UNAVAILABLE_UI_MODES, SeatConfig, normalize_ui_mode
from clue_core.version import CLUE_RELEASE_LABEL, CLUE_VERSION
from clue_storage import ClueRepository
from clue_storage.repository import normalize_player_identity


DEFAULT_TOOL_SNAPSHOT_BUDGET_MS = 250
TURN_METRIC_LIMIT = 256
SOCIAL_MOODS = {"calm", "amused", "annoyed", "guarded", "confident", "wounded"}
SOCIAL_THREAD_KINDS = {"banter", "dispute", "alliance", "flirtation", "meta"}
SOCIAL_THREAD_STATUSES = {"active", "cooling", "resolved"}
MEMORY_FAILURES_LEFT_PENDING = {"missing_agents_sdk", "missing_api_key"}


def _timestamp_slug() -> str:
    """Generate sortable timestamp ids for new game records."""

    return datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")


def _new_game_seed() -> int:
    """Return a fresh setup seed for one game deal."""

    return secrets.randbits(64)


class RepositoryNHPWriteSink:
    """Persist model-facing NHP memory/social writes outside rules authority."""

    def __init__(self, repository: ClueRepository) -> None:
        """Store the repository used for immediate durable write-tool effects."""

        self._repository = repository

    @staticmethod
    def _clamp_delta(value: Any) -> int:
        """Clamp a model-proposed relationship delta into the public tool contract."""

        try:
            return min(max(int(value), -2), 2)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _clamp_social(value: Any, *, minimum: int, maximum: int) -> int:
        """Clamp a current per-game social value after an immediate write."""

        try:
            return min(max(int(value), minimum), maximum)
        except (TypeError, ValueError):
            return minimum

    def _source_context(self, game_id: str, seat_id: str) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Resolve and validate the NHP source seat for a model-facing write."""

        source_game_id = str(game_id or "").strip()
        source_seat_id = str(seat_id or "").strip()
        if not source_game_id or not source_seat_id:
            raise ValueError("source_game_and_seat_required")
        state = self._repository.get_state(source_game_id)
        seat = dict((state.get("seats") or {}).get(source_seat_id) or {})
        if not seat:
            raise ValueError("source_seat_not_found")
        if str(seat.get("seat_kind", "")).strip().lower() == "human":
            raise ValueError("human_seats_cannot_use_nhp_write_tools")
        agent_identity = str(seat.get("character") or "").strip()
        if not agent_identity:
            raise ValueError("source_agent_identity_missing")
        return state, seat | {"seat_id": source_seat_id}, agent_identity

    @staticmethod
    def _target_context(state: dict[str, Any], target_seat_id: str) -> dict[str, str]:
        """Resolve one current-game target seat into durable identity fields."""

        clean_target_id = str(target_seat_id or "").strip()
        if not clean_target_id:
            return {"seat_id": "", "target_kind": "", "target_identity": "", "target_display_name": ""}
        target = dict((state.get("seats") or {}).get(clean_target_id) or {})
        if not target:
            raise ValueError("target_seat_not_found")
        display_name = str(target.get("display_name") or target.get("character") or clean_target_id).strip()
        if str(target.get("seat_kind", "")).strip().lower() == "human":
            target_kind = "hp"
            target_identity = normalize_player_identity(display_name)
        else:
            target_kind = "nhp"
            target_identity = str(target.get("character") or "").strip()
        if not target_identity:
            raise ValueError("target_identity_missing")
        return {
            "seat_id": clean_target_id,
            "target_kind": target_kind,
            "target_identity": target_identity,
            "target_display_name": display_name or target_identity,
        }

    def _apply_current_social_delta(
        self,
        state: dict[str, Any],
        *,
        source_seat_id: str,
        target_seat_id: str,
        affinity_delta: int,
        trust_delta: int,
        friction_delta: int,
    ) -> None:
        """Fold one immediate durable relationship write into current social state."""

        if not target_seat_id:
            return
        social = dict(state.get("social") or {})
        social_seats = dict(social.get("seats") or {})
        if source_seat_id not in social_seats:
            return
        source_social = dict(social_seats.get(source_seat_id) or {})
        relationships = dict(source_social.get("relationships") or {})
        current = dict(relationships.get(target_seat_id) or {"affinity": 0, "trust": 0, "friction": 0})
        current["affinity"] = self._clamp_social(int(current.get("affinity", 0) or 0) + affinity_delta, minimum=-2, maximum=2)
        current["trust"] = self._clamp_social(int(current.get("trust", 0) or 0) + trust_delta, minimum=-2, maximum=2)
        current["friction"] = self._clamp_social(int(current.get("friction", 0) or 0) + friction_delta, minimum=0, maximum=3)
        relationships[target_seat_id] = current
        source_social["relationships"] = relationships
        social_seats[source_seat_id] = source_social
        social["seats"] = social_seats
        state["social"] = social

    def record_note(
        self,
        *,
        game_id: str,
        seat_id: str,
        note_kind: str,
        note_text: str,
        tool_name: str,
        target_seat_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one immediate durable note for the current NHP source seat."""

        try:
            state, _seat, agent_identity = self._source_context(game_id, seat_id)
            target = self._target_context(state, target_seat_id) if str(target_seat_id or "").strip() else {}
            note = self._repository.record_nhp_note(
                agent_identity=agent_identity,
                game_id=str(game_id),
                seat_id=str(seat_id),
                note_kind=note_kind,
                note_text=note_text,
                payload=dict(payload or {}),
                tool_name=tool_name,
                target_kind=str(target.get("target_kind") or ""),
                target_identity=str(target.get("target_identity") or ""),
                target_display_name=str(target.get("target_display_name") or ""),
            )
            return {"status": "ok", "note": note}
        except (KeyError, ValueError) as exc:
            return {"status": "rejected", "reason": str(exc), "tool_name": str(tool_name or "")}

    def update_relationship(
        self,
        *,
        game_id: str,
        seat_id: str,
        target_seat_id: str,
        affinity_delta: int = 0,
        trust_delta: int = 0,
        friction_delta: int = 0,
        note: str = "",
        tool_name: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one immediate bounded durable relationship update and audit note."""

        try:
            state, _seat, agent_identity = self._source_context(game_id, seat_id)
            target = self._target_context(state, target_seat_id)
            if str(target.get("seat_id") or "") == str(seat_id):
                raise ValueError("relationship_target_cannot_be_self")
            clamped = {
                "affinity_delta": self._clamp_delta(affinity_delta),
                "trust_delta": self._clamp_delta(trust_delta),
                "friction_delta": self._clamp_delta(friction_delta),
            }
            relationship = self._repository.upsert_nhp_relationship(
                agent_identity=agent_identity,
                target_kind=target["target_kind"],
                target_identity=target["target_identity"],
                target_display_name=target["target_display_name"],
                source_game_id=str(game_id),
                note=str(note or "").strip(),
                **clamped,
            )
            audit_payload = {
                **dict(payload or {}),
                "target_seat_id": str(target.get("seat_id") or ""),
                "clamped_deltas": clamped,
                "relationship": relationship,
            }
            audit_note = self._repository.record_nhp_note(
                agent_identity=agent_identity,
                game_id=str(game_id),
                seat_id=str(seat_id),
                note_kind="relationship_update",
                note_text=str(note or "Relationship posture updated.").strip(),
                payload=audit_payload,
                tool_name=tool_name,
                target_kind=target["target_kind"],
                target_identity=target["target_identity"],
                target_display_name=target["target_display_name"],
            )
            self._apply_current_social_delta(
                state,
                source_seat_id=str(seat_id),
                target_seat_id=str(target.get("seat_id") or ""),
                **clamped,
            )
            self._repository.save_state_and_events(str(game_id), state=state, events=[])
            return {"status": "ok", "relationship": relationship, "note": audit_note}
        except (KeyError, ValueError) as exc:
            return {"status": "rejected", "reason": str(exc), "tool_name": str(tool_name or "")}


class GameService:
    """High-level gameplay service that bridges web requests, storage, and agents.

    ``GameService`` deliberately owns everything around the rules engine rather
    than inside it: seat-token validation, persistence, analysis metrics,
    social-memory normalization, tool-snapshot generation, and autonomous-seat
    loops. ``GameMaster`` stays small and deterministic; this service handles
    the operational concerns needed by the Flask app.
    """

    def __init__(self, repository: ClueRepository, *, secret_key: str, runtime_overrides: dict[str, Any] | None = None) -> None:
        """Store repository access and build the seat-token serializer."""

        self._repository = repository
        self._serializer = URLSafeSerializer(secret_key, salt="clue-seat-token")
        self._write_sink = RepositoryNHPWriteSink(repository)
        self._agents = AgentRuntime(write_sink=self._write_sink)
        self._runtime_overrides = runtime_overrides if runtime_overrides is not None else {}

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        """Parse one boolean environment knob with a stable fallback."""

        raw = str(os.getenv(name, "")).strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _parse_bool(value: Any, *, default: bool) -> bool:
        """Normalize checkbox, JSON, and query-like boolean values."""

        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if not raw:
            return default
        if raw in {"1", "true", "yes", "on", "enabled"}:
            return True
        if raw in {"0", "false", "no", "off", "disabled"}:
            return False
        return default

    @staticmethod
    def _parse_multiplier(value: Any, *, default: float) -> float:
        """Clamp a chat-chance multiplier into the supported 0..1 range."""

        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return default

    def _runtime_setting_defaults(self) -> dict[str, Any]:
        """Return process env-backed defaults before admin session overrides."""

        return {
            "idle_chat_enabled": self._env_bool("CLUE_IDLE_CHAT_ENABLED", True),
            "proactive_chat_enabled": self._env_bool("CLUE_PROACTIVE_CHAT_ENABLED", True),
            "proactive_chat_chance_multiplier": self._parse_multiplier(
                os.getenv("CLUE_PROACTIVE_CHAT_CHANCE_MULTIPLIER", "0.35"),
                default=0.35,
            ),
        }

    def admin_runtime_settings(self) -> dict[str, Any]:
        """Return the current effective session runtime settings for Administrator Mode."""

        defaults = self._runtime_setting_defaults()
        overrides = {
            key: self._runtime_overrides[key]
            for key in ("idle_chat_enabled", "proactive_chat_enabled", "proactive_chat_chance_multiplier")
            if key in self._runtime_overrides
        }
        effective = defaults | overrides
        return {
            "effective": effective,
            "defaults": defaults,
            "overrides": overrides,
            "reset_on_restart": True,
            "latency_targets_ms": self._latency_targets(),
            "agent_runtime": self._agents.runtime_summary(),
        }

    def update_admin_runtime_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply safe process-local admin overrides for optional chat behavior."""

        data = dict(payload or {})
        if self._parse_bool(data.get("reset"), default=False):
            self._runtime_overrides.clear()
            return self.admin_runtime_settings()

        defaults = self._runtime_setting_defaults()
        for key in ("idle_chat_enabled", "proactive_chat_enabled"):
            if key in data:
                self._runtime_overrides[key] = self._parse_bool(data.get(key), default=bool(defaults[key]))
        if "proactive_chat_chance_multiplier" in data:
            self._runtime_overrides["proactive_chat_chance_multiplier"] = self._parse_multiplier(
                data.get("proactive_chat_chance_multiplier"),
                default=float(defaults["proactive_chat_chance_multiplier"]),
            )
        return self.admin_runtime_settings()

    @staticmethod
    def _latency_targets() -> dict[str, int]:
        """Return the current runtime latency budgets exposed to telemetry."""

        llm_timeout_seconds = max(float(os.getenv("CLUE_LLM_TIMEOUT_SECONDS", "12")), 1.0)
        return {
            "tool_snapshot_ms": int(os.getenv("CLUE_TOOL_SNAPSHOT_BUDGET_MS", str(DEFAULT_TOOL_SNAPSHOT_BUDGET_MS))),
            "llm_turn_ms": int(llm_timeout_seconds * 1000),
            "agent_cycle_ms": int(llm_timeout_seconds * 1000) + int(
                os.getenv("CLUE_TOOL_SNAPSHOT_BUDGET_MS", str(DEFAULT_TOOL_SNAPSHOT_BUDGET_MS))
            ),
        }

    @staticmethod
    def _environment_label() -> str:
        """Describe whether this game is running locally or on App Engine."""

        if os.getenv("GAE_ENV"):
            service = str(os.getenv("GAE_SERVICE", "default")).strip() or "default"
            return f"gae:{service}"
        return "local"

    def _build_analysis_defaults(self, state: dict[str, Any]) -> dict[str, Any]:
        """Create the persisted analysis block used for traces, evals, and diagnostics.

        This block is part of persisted game state so browser diagnostics, tests,
        and later replay/eval work all read the same source of truth.
        """

        seat_mix: dict[str, int] = {}
        for seat in state["seats"].values():
            seat_kind = str(seat["seat_kind"])
            seat_mix[seat_kind] = seat_mix.get(seat_kind, 0) + 1
        return {
            "run_context": {
                "created_at": datetime.now(UTC).isoformat(),
                "environment": self._environment_label(),
                "version": CLUE_VERSION,
                "release_label": CLUE_RELEASE_LABEL,
                "seat_mix": seat_mix,
                "seat_order": list(state["seat_order"]),
            },
            "agent_runtime": self._agents.runtime_summary(),
            "latency_targets_ms": self._latency_targets(),
            "game_metrics": {
                "actions_applied": 0,
                "human_actions": 0,
                "autonomous_actions": 0,
                "rejected_actions": 0,
                "illegal_action_rejects": 0,
                "fallback_count": 0,
                "fallback_rate": 0.0,
                "llm_unavailable_count": 0,
                "guardrail_blocks": 0,
                "sampling_timeouts": 0,
                "latency_budget_breaches": 0,
                "turn_latency_ms_max": 0.0,
                "tool_snapshot_latency_ms_max": 0.0,
                "agent_decision_latency_ms_max": 0.0,
                "accusations_total": 0,
                "accusations_correct": 0,
                "accusation_precision": 0.0,
                "completion_rate": 0.0,
            },
            "turn_metrics": [],
            "latest_private_debug_by_seat": {},
        }

    def _ensure_analysis(self, state: dict[str, Any]) -> dict[str, Any]:
        """Backfill the per-game analysis structure onto a persisted state snapshot."""

        defaults = self._build_analysis_defaults(state)
        analysis = dict(state.get("analysis") or {})
        analysis.setdefault("run_context", defaults["run_context"])
        analysis.setdefault("agent_runtime", defaults["agent_runtime"])
        analysis.setdefault("latency_targets_ms", defaults["latency_targets_ms"])
        analysis.setdefault("game_metrics", defaults["game_metrics"])
        analysis.setdefault("turn_metrics", [])
        analysis.setdefault("latest_private_debug_by_seat", {})
        state["analysis"] = analysis
        return analysis

    @staticmethod
    def _build_social_defaults(state: dict[str, Any]) -> dict[str, Any]:
        """Create the persisted canonical social-memory state for non-human seats."""

        return {
            "last_public_event_index": 0,
            "last_proactive_turn_index": -1,
            "threads": [],
            "seats": {
                seat_id: GameService._blank_social_seat_state(state, seat_id)
                for seat_id, seat in state.get("seats", {}).items()
                if str(seat.get("seat_kind", "")).strip().lower() != "human"
            }
        }

    def _ensure_social(self, state: dict[str, Any]) -> dict[str, Any]:
        """Backfill the canonical social-memory state onto one persisted game snapshot.

        Social state is normalized here rather than trusted as-is from storage so
        older games, partial payloads, or future schema drifts do not crash the
        runtime or silently widen the allowed numeric/state ranges.
        """

        defaults = self._build_social_defaults(state)
        social = dict(state.get("social") or {})
        social.setdefault("last_public_event_index", defaults["last_public_event_index"])
        social.setdefault("last_proactive_turn_index", defaults["last_proactive_turn_index"])
        social["threads"] = [self._normalize_social_thread(item) for item in list(social.get("threads") or [])]
        seat_social = dict(social.get("seats") or {})
        for seat_id, payload in defaults["seats"].items():
            current = dict(seat_social.get(seat_id) or {})
            current.setdefault("last_processed_public_event_index", payload["last_processed_public_event_index"])
            current.setdefault("cooldown_events_remaining", payload["cooldown_events_remaining"])
            current.setdefault("last_chat_event_index", payload["last_chat_event_index"])
            current.setdefault("mood", payload["mood"])
            current.setdefault("focus_seat_id", payload["focus_seat_id"])
            current.setdefault("speaking_streak", payload["speaking_streak"])
            current["recent_intents"] = list(current.get("recent_intents") or payload["recent_intents"])[-4:]
            relationships = dict(current.get("relationships") or {})
            for other_seat_id, relationship in payload["relationships"].items():
                current_relationship = dict(relationships.get(other_seat_id) or {})
                current_relationship.setdefault("affinity", int(relationship.get("affinity") or 0))
                current_relationship.setdefault("trust", int(relationship.get("trust") or 0))
                current_relationship.setdefault("friction", int(relationship.get("friction") or 0))
                relationships[other_seat_id] = self._normalize_relationship_state(current_relationship)
            current["relationships"] = relationships
            current = self._normalize_social_seat_state(current)
            seat_social[seat_id] = current
        social["seats"] = seat_social
        state["social"] = social
        return social

    def _merge_latest_social_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Preserve immediate write-tool social updates before saving local state."""

        try:
            latest_state = self._repository.get_state(str(state.get("game_id") or ""))
        except KeyError:
            return state
        latest_social = latest_state.get("social")
        if latest_social:
            state["social"] = latest_social
            self._ensure_social(state)
        return state

    @staticmethod
    def _blank_social_seat_state(state: dict[str, Any], seat_id: str) -> dict[str, Any]:
        """Build the default per-seat social-memory state for one non-human seat."""

        return {
            "last_processed_public_event_index": 0,
            "cooldown_events_remaining": 0,
            "last_chat_event_index": 0,
            "mood": "calm",
            "focus_seat_id": "",
            "speaking_streak": 0,
            "recent_intents": [],
            "relationships": {
                other_seat_id: GameService._seed_relationship_state(state, seat_id, other_seat_id)
                for other_seat_id in state.get("seat_order", [])
                if other_seat_id != seat_id
            },
        }

    @staticmethod
    def _seed_relationship_state(state: dict[str, Any], seat_id: str, other_seat_id: str) -> dict[str, int]:
        """Seed one relationship state from the richer YAML persona hints."""

        seat = dict((state.get("seats") or {}).get(seat_id) or {})
        other = dict((state.get("seats") or {}).get(other_seat_id) or {})
        relationship_hint = persona_relationship_map(str(seat.get("character") or "")).get(str(other.get("character") or ""), {})
        stance = str(relationship_hint.get("stance") or "").strip().lower()
        try:
            chemistry = min(max(int(relationship_hint.get("chemistry") or 3), 1), 5)
        except (TypeError, ValueError):
            chemistry = 3
        affinity = chemistry - 3
        trust = 0
        friction = 0 if chemistry >= 4 else (1 if chemistry == 3 else 2)
        if any(word in stance for word in ("ally", "fond", "respect", "warm", "protect")):
            trust += 1
            affinity += 1
        if any(word in stance for word in ("flirt", "charm")):
            affinity += 1
        if any(word in stance for word in ("wary", "skept", "dismiss", "jealous", "rival", "hostile", "resent", "cold")):
            trust -= 1
            friction += 1
        return {
            "affinity": GameService._clamp_value(affinity, minimum=-2, maximum=2),
            "trust": GameService._clamp_value(trust, minimum=-2, maximum=2),
            "friction": GameService._clamp_value(friction, minimum=0, maximum=3),
        }

    @staticmethod
    def _normalize_relationship_state(relationship: dict[str, Any]) -> dict[str, int]:
        """Clamp one persisted relationship row into the supported numeric ranges."""

        return {
            "affinity": GameService._clamp_value(relationship.get("affinity", 0), minimum=-2, maximum=2),
            "trust": GameService._clamp_value(relationship.get("trust", 0), minimum=-2, maximum=2),
            "friction": GameService._clamp_value(relationship.get("friction", 0), minimum=0, maximum=3),
        }

    @staticmethod
    def _normalize_social_seat_state(seat_social: dict[str, Any]) -> dict[str, Any]:
        """Clamp and normalize one persisted per-seat social-memory record."""

        mood = str(seat_social.get("mood") or "calm")
        if mood not in SOCIAL_MOODS:
            mood = "calm"
        return {
            "last_processed_public_event_index": max(int(seat_social.get("last_processed_public_event_index", 0) or 0), 0),
            "cooldown_events_remaining": max(int(seat_social.get("cooldown_events_remaining", 0) or 0), 0),
            "last_chat_event_index": max(int(seat_social.get("last_chat_event_index", 0) or 0), 0),
            "mood": mood,
            "focus_seat_id": str(seat_social.get("focus_seat_id") or ""),
            "speaking_streak": max(int(seat_social.get("speaking_streak", 0) or 0), 0),
            "recent_intents": [str(item) for item in list(seat_social.get("recent_intents") or []) if str(item).strip()][-4:],
            "relationships": {
                str(other_seat_id): GameService._normalize_relationship_state(dict(payload or {}))
                for other_seat_id, payload in dict(seat_social.get("relationships") or {}).items()
                if str(other_seat_id).strip()
            },
        }

    @staticmethod
    def _normalize_social_thread(thread: dict[str, Any]) -> dict[str, Any]:
        """Clamp and normalize one persisted social thread record."""

        kind = str(thread.get("kind") or "meta")
        status = str(thread.get("status") or "active")
        heat_minimum = 0 if status == "resolved" else 1
        return {
            "thread_id": str(thread.get("thread_id") or ""),
            "kind": kind if kind in SOCIAL_THREAD_KINDS else "meta",
            "topic": str(thread.get("topic") or ""),
            "participants": [str(item) for item in list(thread.get("participants") or []) if str(item).strip()],
            "heat": GameService._clamp_value(thread.get("heat", 1), minimum=heat_minimum, maximum=3),
            "status": status if status in SOCIAL_THREAD_STATUSES else "active",
            "burst_count": GameService._clamp_value(thread.get("burst_count", 0), minimum=0, maximum=2),
            "last_event_index": max(int(thread.get("last_event_index", 0) or 0), 0),
        }

    @staticmethod
    def _clamp_value(value: Any, *, minimum: int, maximum: int) -> int:
        """Clamp one integer-like value into the requested inclusive range."""

        try:
            return min(max(int(value), minimum), maximum)
        except (TypeError, ValueError):
            return minimum

    @staticmethod
    def _durable_relationship_signal(value: Any) -> int:
        """Map durable relationship memory onto a small per-game social nudge."""

        numeric = GameService._clamp_value(value, minimum=-5, maximum=5)
        if numeric >= 2:
            return 1
        if numeric <= -2:
            return -1
        return 0

    @staticmethod
    def _seat_id_for_durable_target(state: dict[str, Any], *, target_kind: str, target_identity: str) -> str:
        """Resolve a durable relationship target identity into this game's seat id."""

        normalized_kind = str(target_kind or "").strip().lower()
        normalized_target = str(target_identity or "").strip()
        if not normalized_target:
            return ""
        for seat_id, seat in dict(state.get("seats") or {}).items():
            if normalized_kind == "nhp" and str(seat.get("character") or "") == normalized_target:
                return str(seat_id)
            if normalized_kind == "hp" and normalize_player_identity(str(seat.get("display_name") or "")) == normalized_target:
                return str(seat_id)
        return ""

    def _apply_durable_relationships_to_social(self, state: dict[str, Any]) -> None:
        """Seed per-game social posture from durable cross-game relationships."""

        social = self._ensure_social(state)
        social_seats = dict(social.get("seats") or {})
        for seat_id, seat in dict(state.get("seats") or {}).items():
            if str(seat.get("seat_kind", "")).strip().lower() == "human":
                continue
            seat_social = dict(social_seats.get(seat_id) or {})
            relationships = dict(seat_social.get("relationships") or {})
            for durable in self._repository.list_nhp_relationships(agent_identity=str(seat.get("character") or "")):
                target_seat_id = self._seat_id_for_durable_target(
                    state,
                    target_kind=str(durable.get("target_kind") or ""),
                    target_identity=str(durable.get("target_identity") or ""),
                )
                if not target_seat_id or target_seat_id == seat_id:
                    continue
                current = dict(relationships.get(target_seat_id) or {"affinity": 0, "trust": 0, "friction": 0})
                current["affinity"] = self._clamp_value(
                    int(current.get("affinity", 0) or 0) + self._durable_relationship_signal(durable.get("affinity")),
                    minimum=-2,
                    maximum=2,
                )
                current["trust"] = self._clamp_value(
                    int(current.get("trust", 0) or 0) + self._durable_relationship_signal(durable.get("trust")),
                    minimum=-2,
                    maximum=2,
                )
                current["friction"] = self._clamp_value(
                    int(current.get("friction", 0) or 0) + (1 if int(durable.get("friction") or 0) >= 2 else 0),
                    minimum=0,
                    maximum=3,
                )
                relationships[target_seat_id] = current
            seat_social["relationships"] = relationships
            social_seats[seat_id] = self._normalize_social_seat_state(seat_social)
        social["seats"] = social_seats
        state["social"] = social

    def _memory_context_for_seat(self, state: dict[str, Any], seat_id: str) -> dict[str, Any]:
        """Build durable memory context for internal NHP runtime snapshots only."""

        seat = dict((state.get("seats") or {}).get(seat_id) or {})
        agent_identity = str(seat.get("character") or "")
        if not agent_identity or str(seat.get("seat_kind", "")).strip().lower() == "human":
            return {}
        relationships = []
        for item in self._repository.list_nhp_relationships(agent_identity=agent_identity):
            relationship = dict(item)
            relationship["current_target_seat_id"] = self._seat_id_for_durable_target(
                state,
                target_kind=str(item.get("target_kind") or ""),
                target_identity=str(item.get("target_identity") or ""),
            )
            relationships.append(relationship)
        return {
            "agent_identity": agent_identity,
            "ready_memories": self._repository.ready_nhp_memory_for_agent(agent_identity, limit=5),
            "relationships": relationships,
            "recent_notes": self._repository.recent_nhp_notes_for_agent(agent_identity, limit=8),
        }

    @staticmethod
    def _normalize_memory_relationship_update(
        state: dict[str, Any],
        *,
        source_seat_id: str,
        update: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize one model-authored relationship update for durable storage."""

        raw_kind = str(update.get("target_kind") or "").strip().lower()
        raw_identity = str(update.get("target_identity") or "").strip()
        raw_display = str(update.get("target_display_name") or raw_identity).strip()
        source_character = str(dict((state.get("seats") or {}).get(source_seat_id) or {}).get("character") or "")
        matched_seat: dict[str, Any] = {}
        for candidate_id, candidate in dict(state.get("seats") or {}).items():
            aliases = {
                str(candidate_id).casefold(),
                str(candidate.get("display_name") or "").casefold(),
                str(candidate.get("character") or "").casefold(),
            }
            if raw_identity.casefold() in aliases or raw_display.casefold() in aliases:
                matched_seat = dict(candidate)
                break
        if raw_kind not in {"nhp", "hp"}:
            raw_kind = "hp" if str(matched_seat.get("seat_kind") or "") == "human" else "nhp"
        if raw_kind == "nhp":
            target_identity = str(matched_seat.get("character") or raw_identity)
            target_display = str(matched_seat.get("display_name") or raw_display or target_identity)
            if target_identity == source_character:
                return {}
        else:
            display = str(matched_seat.get("display_name") or raw_display or raw_identity)
            target_identity = normalize_player_identity(display or raw_identity)
            target_display = display
        if not target_identity:
            return {}
        return {
            "target_kind": raw_kind,
            "target_identity": target_identity,
            "target_display_name": target_display,
            "affinity_delta": GameService._clamp_value(update.get("affinity_delta"), minimum=-2, maximum=2),
            "trust_delta": GameService._clamp_value(update.get("trust_delta"), minimum=-2, maximum=2),
            "friction_delta": GameService._clamp_value(update.get("friction_delta"), minimum=-2, maximum=2),
            "note": str(update.get("note") or "").strip(),
        }

    @staticmethod
    def _normalize_memory_summary(state: dict[str, Any], *, seat_id: str, summary: dict[str, Any]) -> dict[str, Any]:
        """Clamp the LLM-authored durable memory payload into the persisted shape."""

        normalized_updates = []
        for update in list(summary.get("relationship_updates") or []):
            if not isinstance(update, dict):
                continue
            normalized = GameService._normalize_memory_relationship_update(state, source_seat_id=seat_id, update=update)
            if normalized:
                normalized_updates.append(normalized)
        return {
            "first_person_summary": str(summary.get("first_person_summary") or "").strip(),
            "strategic_lessons": [str(item).strip() for item in list(summary.get("strategic_lessons") or []) if str(item).strip()][:8],
            "social_observations": [str(item).strip() for item in list(summary.get("social_observations") or []) if str(item).strip()][:8],
            "grudges": [str(item).strip() for item in list(summary.get("grudges") or []) if str(item).strip()][:8],
            "favors": [str(item).strip() for item in list(summary.get("favors") or []) if str(item).strip()][:8],
            "future_play_cues": [str(item).strip() for item in list(summary.get("future_play_cues") or []) if str(item).strip()][:8],
            "relationship_updates": normalized_updates[:12],
        }

    def _ensure_memory_jobs_for_completed_game(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Create durable memory jobs for every NHP seat in a completed game."""

        if str(state.get("status") or "") != "complete":
            return []
        game_id = str(state.get("game_id") or "")
        jobs = []
        for seat_id, seat in dict(state.get("seats") or {}).items():
            if str(seat.get("seat_kind", "")).strip().lower() == "human":
                continue
            jobs.append(
                self._repository.ensure_nhp_memory_job(
                    game_id=game_id,
                    seat_id=str(seat_id),
                    character=str(seat.get("character") or ""),
                    display_name=str(seat.get("display_name") or seat.get("character") or seat_id),
                )
            )
        return jobs

    def _attempt_memory_job(self, job: dict[str, Any], *, allow_retry: bool = False) -> dict[str, Any]:
        """Attempt one LLM-authored durable memory job and persist its outcome."""

        status = str(job.get("status") or "")
        if status == "ready":
            return {"memory_id": job.get("id"), "status": "skipped", "reason": "already_ready"}
        if not allow_retry and (status != "pending" or int(job.get("retry_count") or 0) > 0):
            return {"memory_id": job.get("id"), "status": "skipped", "reason": "not_fresh_pending"}
        game_id = str(job.get("source_game_id") or "")
        seat_id = str(job.get("source_seat_id") or "")
        state = self._repository.get_state(game_id)
        self._ensure_analysis(state)
        self._ensure_social(state)
        seat_row = next((item for item in self._repository.list_seats(game_id) if item["seat_id"] == seat_id), {})
        if not seat_row or seat_id not in dict(state.get("seats") or {}):
            failed = self._repository.mark_nhp_memory_failure(str(job["id"]), reason="memory_source_seat_missing", status="failed")
            return {"memory_id": failed["id"], "status": failed["status"], "reason": failed["failure_reason"]}
        seat = dict(seat_row) | dict(state["seats"][seat_id]) | {"seat_id": seat_id, "notebook": dict(seat_row.get("notebook") or {})}
        snapshot = self._build_internal_snapshot(game_id, seat_id)
        try:
            decision = self._agents.summarize_memory(seat=seat, snapshot=snapshot)
            summary = self._normalize_memory_summary(state, seat_id=seat_id, summary=decision.summary)
            if not summary["first_person_summary"]:
                raise MemorySummaryError("invalid_memory_summary")
            ready = self._repository.mark_nhp_memory_ready(
                str(job["id"]),
                summary=summary,
                model_meta={
                    "rationale_private": decision.rationale_private,
                    "agent_meta": dict(decision.agent_meta or {}),
                    "debug_private": dict(decision.debug_private or {}),
                },
            )
            for update in summary["relationship_updates"]:
                self._repository.upsert_nhp_relationship(
                    agent_identity=str(ready["agent_identity"]),
                    source_game_id=game_id,
                    **update,
                )
            return {"memory_id": ready["id"], "status": ready["status"], "relationship_updates": len(summary["relationship_updates"])}
        except MemorySummaryError as exc:
            next_status = "pending" if exc.reason in MEMORY_FAILURES_LEFT_PENDING else "failed"
            failed = self._repository.mark_nhp_memory_failure(str(job["id"]), reason=exc.reason, status=next_status)
            return {"memory_id": failed["id"], "status": failed["status"], "reason": failed["failure_reason"]}
        except Exception as exc:
            failed = self._repository.mark_nhp_memory_failure(str(job["id"]), reason=f"memory_summary_error: {exc}", status="failed")
            return {"memory_id": failed["id"], "status": failed["status"], "reason": failed["failure_reason"]}

    def _finalize_game_if_complete(self, game_id: str, state: dict[str, Any]) -> None:
        """Run completed-game durable memory hooks and then clean up LLM sessions."""

        if str(state.get("status") or "") != "complete":
            return
        for job in self._ensure_memory_jobs_for_completed_game(state):
            self._attempt_memory_job(job)
        self._cleanup_llm_sessions_if_complete(state)

    def _cleanup_llm_sessions_if_complete(self, state: dict[str, Any]) -> None:
        """Clear local LLM seat sessions after a game reaches a terminal state."""

        if str(state.get("status") or "") != "complete":
            return
        seats = [
            {
                "seat_id": seat_id,
                "seat_kind": seat.get("seat_kind", ""),
                "agent_model": seat.get("agent_model", ""),
                "agent_profile": seat.get("agent_profile", ""),
                "agent_chat_model": seat.get("agent_chat_model", ""),
                "agent_chat_profile": seat.get("agent_chat_profile", ""),
            }
            for seat_id, seat in state.get("seats", {}).items()
        ]
        self._agents.clear_llm_sessions(game_id=str(state.get("game_id", "")), seats=seats)

    @staticmethod
    def _trace_event(
        event_type: str,
        *,
        message: str,
        payload: dict[str, Any],
        visibility: str = "public",
    ) -> dict[str, Any]:
        """Create one telemetry event without changing the core rules engine."""

        return make_event(event_type, payload=payload, message=message, visibility=visibility)

    def _record_turn_metric(self, state: dict[str, Any], metric: dict[str, Any], *, private_debug: dict[str, Any] | None = None) -> None:
        """Persist one per-turn metric row and roll its values into game-level aggregates."""

        analysis = self._ensure_analysis(state)
        turn_metrics = list(analysis.get("turn_metrics") or [])
        turn_metrics.append(metric)
        analysis["turn_metrics"] = turn_metrics[-TURN_METRIC_LIMIT:]
        if private_debug and metric.get("seat_id"):
            latest_private_debug = dict(analysis.get("latest_private_debug_by_seat") or {})
            latest_private_debug[str(metric["seat_id"])] = private_debug
            analysis["latest_private_debug_by_seat"] = latest_private_debug

        aggregates = dict(analysis.get("game_metrics") or {})
        if metric.get("rejected"):
            aggregates["rejected_actions"] = int(aggregates.get("rejected_actions", 0)) + 1
            if str(metric.get("rejection_kind") or "illegal_action") == "illegal_action":
                aggregates["illegal_action_rejects"] = int(aggregates.get("illegal_action_rejects", 0)) + 1
        else:
            aggregates["actions_applied"] = int(aggregates.get("actions_applied", 0)) + 1
            actor = str(metric.get("actor", "human"))
            if actor == "human":
                aggregates["human_actions"] = int(aggregates.get("human_actions", 0)) + 1
            else:
                aggregates["autonomous_actions"] = int(aggregates.get("autonomous_actions", 0)) + 1
        if metric.get("fallback_used"):
            aggregates["fallback_count"] = int(aggregates.get("fallback_count", 0)) + 1
        if str(metric.get("rejection_kind") or "") == "llm_unavailable":
            aggregates["llm_unavailable_count"] = int(aggregates.get("llm_unavailable_count", 0)) + 1
        if metric.get("guardrail_blocks"):
            aggregates["guardrail_blocks"] = int(aggregates.get("guardrail_blocks", 0)) + int(metric["guardrail_blocks"])
        if metric.get("sampling_timed_out"):
            aggregates["sampling_timeouts"] = int(aggregates.get("sampling_timeouts", 0)) + 1
        if metric.get("latency_budget_breached"):
            aggregates["latency_budget_breaches"] = int(aggregates.get("latency_budget_breaches", 0)) + 1
        aggregates["turn_latency_ms_max"] = round(
            max(float(aggregates.get("turn_latency_ms_max", 0.0)), float(metric.get("latency_ms", 0.0))),
            2,
        )
        aggregates["tool_snapshot_latency_ms_max"] = round(
            max(float(aggregates.get("tool_snapshot_latency_ms_max", 0.0)), float(metric.get("tool_snapshot_latency_ms", 0.0))),
            2,
        )
        aggregates["agent_decision_latency_ms_max"] = round(
            max(float(aggregates.get("agent_decision_latency_ms_max", 0.0)), float(metric.get("agent_decision_latency_ms", 0.0))),
            2,
        )
        if metric.get("action") == "accuse":
            aggregates["accusations_total"] = int(aggregates.get("accusations_total", 0)) + 1
            if metric.get("accusation_correct"):
                aggregates["accusations_correct"] = int(aggregates.get("accusations_correct", 0)) + 1
        accusations_total = int(aggregates.get("accusations_total", 0))
        aggregates["accusation_precision"] = round(
            (int(aggregates.get("accusations_correct", 0)) / accusations_total) if accusations_total else 0.0,
            4,
        )
        autonomous_actions = int(aggregates.get("autonomous_actions", 0))
        aggregates["fallback_rate"] = round(
            (int(aggregates.get("fallback_count", 0)) / autonomous_actions) if autonomous_actions else 0.0,
            4,
        )
        aggregates["completion_rate"] = 1.0 if state.get("status") == "complete" else 0.0
        analysis["game_metrics"] = aggregates

    def create_game(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create one new game, persist it, and immediately run any autonomous seats.

        Game creation is also where legacy seat kinds are normalized, YAML model
        profiles are assigned, telemetry context is seeded, and invite tokens are
        minted. The returned payload is intentionally minimal: game id, title,
        and per-seat invitation links.
        """

        title = str(payload.get("title", "")).strip() or "Clue Table"
        default_ui_mode = self._ui_mode_from_payload(payload)
        requested_seats = list(payload.get("seats") or [])
        if requested_seats:
            seat_configs = self._seat_configs_from_payload(requested_seats, default_ui_mode=default_ui_mode)
        else:
            seat_configs = self._default_seats(ui_mode=default_ui_mode)
        game_id = f"clue_{_timestamp_slug()}"
        self._apply_llm_profiles(game_id, seat_configs)
        seed = _new_game_seed()
        hidden_setup = build_hidden_setup(seat_configs, seed=seed)
        state = build_initial_state(game_id, title, seat_configs, hidden_setup)
        state["ui_mode"] = default_ui_mode
        state["analysis"] = self._build_analysis_defaults(state)
        self._ensure_social(state)
        self._apply_durable_relationships_to_social(state)
        tokens = []
        seat_links = []
        for seat in seat_configs:
            token = self._serializer.dumps(
                {
                    "game_id": game_id,
                    "seat_id": seat.seat_id,
                    "nonce": secrets.token_urlsafe(8),
                }
            )
            tokens.append({"seat_id": seat.seat_id, "token": token})
            seat_links.append(
                {
                    "seat_id": seat.seat_id,
                    "display_name": seat.display_name,
                    "character": seat.character,
                    "seat_kind": seat.seat_kind,
                    "agent_profile": seat.agent_profile,
                    "agent_chat_profile": seat.agent_chat_profile,
                    "ui_mode": seat.ui_mode,
                    "url": f"join/{token}",
                }
            )
        initial_events = [
            make_event("game_created", payload={"game_id": game_id, "title": title}, message=f"{title} was created."),
            make_event(
                "turn_started",
                payload={"seat_id": state["active_seat_id"], "turn_index": state["turn_index"]},
                message=f"It is now {state['seats'][state['active_seat_id']]['display_name']}'s turn.",
            ),
            self._trace_event(
                "trace_game_context",
                message=f"Telemetry initialized for {title}.",
                payload=state["analysis"]["run_context"]
                | {
                    "latency_targets_ms": state["analysis"]["latency_targets_ms"],
                    "agent_runtime": state["analysis"]["agent_runtime"],
                },
            ),
        ]
        self._repository.create_game(
            game_id=game_id,
            title=title,
            config={
                "game_id": game_id,
                "title": title,
                "ui_mode": default_ui_mode,
                "seed": seed,
                "seats": [seat.to_dict() for seat in seat_configs],
            },
            setup=hidden_setup,
            state=state,
            seats=[seat.to_dict() for seat in seat_configs],
            seat_tokens=tokens,
            events=initial_events,
        )
        self.maybe_run_agents(game_id)
        return {"game_id": game_id, "title": title, "seat_links": seat_links}

    def join_by_token(self, token: str) -> dict[str, Any]:
        """Resolve one seat token and mark the seat as having joined the table."""

        seat = self.resolve_token(token)
        self._repository.mark_seat_seen(seat["game_id"], seat["seat_id"])
        return seat

    def resolve_token(self, token: str) -> dict[str, Any]:
        """Validate a signed seat token against the persisted token record."""

        try:
            payload = self._serializer.loads(token)
        except BadSignature as exc:
            raise KeyError("Invalid seat token.") from exc
        seat = self._repository.get_seat_by_token(token)
        if seat is None:
            raise KeyError("Unknown seat token.")
        if seat["game_id"] != payload["game_id"] or seat["seat_id"] != payload["seat_id"]:
            raise KeyError("Seat token did not match stored seat context.")
        return seat

    def snapshot_for_token(self, token: str, *, since_event_index: int = 0) -> dict[str, Any]:
        """Return the public/private snapshot visible to one seat token.

        Snapshot reads may also advance idle social chatter, but they do so only
        when no autonomous gameplay action is pending. This keeps passive browser
        refreshes from racing ahead of queued turns.
        """

        seat = self.resolve_token(token)
        self.maybe_run_idle_chat(seat["game_id"])
        state = self._repository.get_state(seat["game_id"])
        self._ensure_analysis(state)
        self._ensure_social(state)
        self._finalize_game_if_complete(seat["game_id"], state)
        visible_events = self._repository.visible_events(seat["game_id"], seat_id=seat["seat_id"], since_event_index=since_event_index)
        return build_filtered_snapshot(
            state,
            seat_id=seat["seat_id"],
            visible_events=visible_events,
            notebook=seat["notebook"],
        )

    def submit_action(self, token: str, action: dict[str, Any]) -> dict[str, Any]:
        """Apply one human-seat action, then continue any autonomous follow-up turns.

        This is the main human-seat write path. It records rejected actions in
        persisted metrics, saves newly emitted events, then runs the autonomous
        loop until a human seat must respond again.
        """

        seat = self.resolve_token(token)
        state = self._repository.get_state(seat["game_id"])
        self._ensure_analysis(state)
        self._ensure_social(state)
        started = time.perf_counter()
        game = GameMaster(state)
        try:
            new_state, events = game.apply_action(seat["seat_id"], action)
        except ValueError as exc:
            metric = {
                "recorded_at": datetime.now(UTC).isoformat(),
                "turn_index": int(state["turn_index"]),
                "seat_id": seat["seat_id"],
                "seat_kind": str(state["seats"][seat["seat_id"]]["seat_kind"]),
                "actor": "human",
                "action": str(action.get("action", "")),
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "tool_snapshot_latency_ms": 0.0,
                "agent_decision_latency_ms": 0.0,
                "fallback_used": False,
                "guardrail_blocks": 0,
                "sampling_timed_out": False,
                "latency_budget_breached": False,
                "rejected": True,
                "error": str(exc),
            }
            self._record_turn_metric(state, metric)
            self._repository.save_state_and_events(
                seat["game_id"],
                state=state,
                events=[
                    self._trace_event(
                        "trace_action_rejected",
                        message=f"{seat['display_name']} submitted an illegal {action.get('action', 'unknown')} action.",
                        payload=metric,
                        visibility=f"seat:{seat['seat_id']}",
                    )
                ],
            )
            self._finalize_game_if_complete(seat["game_id"], state)
            raise
        public_chat = sanitize_public_chat(str(action.get("text", "")).strip()) if action.get("action") != "send_chat" else ""
        guardrail_blocks = 0
        if public_chat:
            chat_game = GameMaster(new_state)
            new_state, chat_events = chat_game.apply_action(seat["seat_id"], {"action": "send_chat", "text": public_chat})
            events.extend(chat_events)
            if public_chat != str(action.get("text", "")).strip():
                guardrail_blocks = 1
                events.append(
                    self._trace_event(
                        "trace_guardrail_blocked",
                        message=f"Public chat from {seat['display_name']} was sanitized before posting.",
                        payload={"seat_id": seat["seat_id"], "action": str(action.get("action", ""))},
                    )
                )
        metric = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "turn_index": int(new_state["turn_index"]),
            "seat_id": seat["seat_id"],
            "seat_kind": str(new_state["seats"][seat["seat_id"]]["seat_kind"]),
            "actor": "human",
            "action": str(action.get("action", "")),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "tool_snapshot_latency_ms": 0.0,
            "agent_decision_latency_ms": 0.0,
            "fallback_used": False,
            "guardrail_blocks": guardrail_blocks,
            "sampling_timed_out": False,
            "latency_budget_breached": False,
            "rejected": False,
            "accusation_correct": bool(action.get("action") == "accuse" and new_state.get("winner_seat_id") == seat["seat_id"]),
        }
        self._record_turn_metric(new_state, metric)
        events.extend(
            [
                self._trace_event(
                    "trace_turn_metric",
                    message=f"Turn metric recorded for {seat['display_name']} ({action.get('action', 'unknown')}).",
                    payload=metric,
                    visibility=f"seat:{seat['seat_id']}",
                )
            ]
        )
        self._repository.save_state_and_events(seat["game_id"], state=new_state, events=events)
        self._finalize_game_if_complete(seat["game_id"], new_state)
        self.maybe_run_agents(seat["game_id"])
        return self.snapshot_for_token(token)

    def update_notebook(self, token: str, notebook: dict[str, Any]) -> dict[str, Any]:
        """Persist a seat notebook change and return the refreshed filtered snapshot."""

        seat = self.resolve_token(token)
        self._repository.update_notebook(seat["game_id"], seat["seat_id"], notebook)
        return self.snapshot_for_token(token)

    @staticmethod
    def _seat_mix_label(seat_mix: dict[str, int]) -> str:
        """Format one compact seat-mix summary for admin tables."""

        order = ("human", "llm", "heuristic", "np")
        parts = [f"{seat_mix[key]} {key}" for key in order if int(seat_mix.get(key, 0) or 0)]
        parts.extend(f"{value} {key}" for key, value in sorted(seat_mix.items()) if key not in order and int(value or 0))
        return ", ".join(parts) or "No seats"

    def _admin_game_summary_from_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Shape one saved game into the dashboard's compact review row."""

        state = dict(record.get("state") or {})
        analysis = dict(state.get("analysis") or {})
        metrics = dict(analysis.get("game_metrics") or {})
        seats = dict(state.get("seats") or {})
        seat_mix: dict[str, int] = {}
        for seat in seats.values():
            seat_kind = str(dict(seat or {}).get("seat_kind") or "unknown")
            seat_mix[seat_kind] = seat_mix.get(seat_kind, 0) + 1
        winner_seat_id = str(state.get("winner_seat_id") or "")
        winner = dict(seats.get(winner_seat_id) or {})
        active_seat_id = str(state.get("active_seat_id") or "")
        active = dict(seats.get(active_seat_id) or {})
        hidden = dict(state.get("hidden") or {})
        case_file = dict(hidden.get("case_file") or {})
        return {
            "id": str(record.get("id") or ""),
            "title": str(record.get("title") or ""),
            "status": str(record.get("status") or state.get("status") or ""),
            "created_at": str(record.get("created_at") or ""),
            "updated_at": str(record.get("updated_at") or ""),
            "phase": str(state.get("phase") or ""),
            "turn_index": int(state.get("turn_index") or 0),
            "active_seat_id": active_seat_id,
            "active_display_name": str(active.get("display_name") or active.get("character") or active_seat_id),
            "winner_seat_id": winner_seat_id,
            "winner_display_name": str(winner.get("display_name") or winner.get("character") or winner_seat_id),
            "case_file": case_file,
            "seat_count": len(seats),
            "seat_mix": seat_mix,
            "seat_mix_label": self._seat_mix_label(seat_mix),
            "actions_applied": int(metrics.get("actions_applied", 0) or 0),
            "autonomous_actions": int(metrics.get("autonomous_actions", 0) or 0),
            "llm_unavailable_count": int(metrics.get("llm_unavailable_count", 0) or 0),
            "sampling_timeouts": int(metrics.get("sampling_timeouts", 0) or 0),
            "latency_budget_breaches": int(metrics.get("latency_budget_breaches", 0) or 0),
            "turn_latency_ms_max": float(metrics.get("turn_latency_ms_max", 0.0) or 0.0),
            "agent_decision_latency_ms_max": float(metrics.get("agent_decision_latency_ms_max", 0.0) or 0.0),
            "tool_snapshot_latency_ms_max": float(metrics.get("tool_snapshot_latency_ms_max", 0.0) or 0.0),
            "completion_rate": float(metrics.get("completion_rate", 0.0) or 0.0),
            "accusation_precision": float(metrics.get("accusation_precision", 0.0) or 0.0),
            "run_context": dict(analysis.get("run_context") or {}),
        }

    def _admin_game_summary(self, game: dict[str, Any]) -> dict[str, Any]:
        """Load one listed game's state and return a compact dashboard row."""

        record = self._repository.get_game_record(str(game.get("id") or ""))
        if record is None:
            return {
                **dict(game),
                "phase": "",
                "turn_index": 0,
                "winner_display_name": "",
                "seat_mix_label": "Unavailable",
                "llm_unavailable_count": 0,
                "sampling_timeouts": 0,
                "latency_budget_breaches": 0,
                "turn_latency_ms_max": 0.0,
                "autonomous_actions": 0,
            }
        return self._admin_game_summary_from_record(record)

    @staticmethod
    def _admin_overview(
        *,
        games: list[dict[str, Any]],
        pending_memory: list[dict[str, Any]],
        nhp_memory: list[dict[str, Any]],
        notes: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate the headline numbers for the Superplayer admin dashboard."""

        return {
            "saved_games": len(games),
            "active_games": sum(1 for game in games if str(game.get("status") or "") == "active"),
            "complete_games": sum(1 for game in games if str(game.get("status") or "") == "complete"),
            "pending_memory_jobs": sum(1 for job in pending_memory if str(job.get("status") or "") == "pending"),
            "failed_memory_jobs": sum(1 for job in pending_memory if str(job.get("status") or "") == "failed"),
            "ready_memory_rows": sum(1 for job in nhp_memory if str(job.get("status") or "") == "ready"),
            "durable_notes": len(notes),
            "relationships": len(relationships),
            "llm_failures": sum(int(game.get("llm_unavailable_count", 0) or 0) for game in games),
            "sampling_timeouts": sum(int(game.get("sampling_timeouts", 0) or 0) for game in games),
            "latency_budget_breaches": sum(int(game.get("latency_budget_breaches", 0) or 0) for game in games),
            "turn_latency_ms_max": max((float(game.get("turn_latency_ms_max", 0.0) or 0.0) for game in games), default=0.0),
        }

    def admin_dashboard(self) -> dict[str, Any]:
        """Return shaped Superplayer Administrator Mode dashboard data."""

        games = [self._admin_game_summary(game) for game in self._repository.list_games(limit=100)]
        nhp_memory = self._repository.list_nhp_memory(limit=100)
        nhp_notes = self._repository.list_nhp_notes(limit=100)
        pending_memory = self._repository.list_pending_nhp_memory_jobs(include_failed=True, limit=100)
        relationships = self._repository.list_nhp_relationships(limit=250)
        return {
            "overview": self._admin_overview(
                games=games,
                pending_memory=pending_memory,
                nhp_memory=nhp_memory,
                notes=nhp_notes,
                relationships=relationships,
            ),
            "runtime_settings": self.admin_runtime_settings(),
            "games": games,
            "nhp_memory": nhp_memory,
            "nhp_notes": nhp_notes,
            "pending_memory": pending_memory,
            "relationships": relationships,
            "nhp_history": self._repository.list_nhp_history(limit=100),
            "human_history": self._repository.list_human_player_history(limit=100),
        }

    def admin_game_detail(self, game_id: str) -> dict[str, Any]:
        """Return a full admin-authorized saved-game detail payload."""

        return self._repository.admin_game_detail(game_id)

    def admin_game_review(self, game_id: str) -> dict[str, Any]:
        """Return one saved game with admin-truth fields shaped for the UI."""

        detail = self._repository.admin_game_detail(game_id)
        summary = self._admin_game_summary_from_record(detail)
        state = dict(detail.get("state") or {})
        analysis = dict(state.get("analysis") or {})
        hidden = dict(state.get("hidden") or {})
        hands = dict(hidden.get("hands") or {})
        seats = []
        state_seats = dict(state.get("seats") or {})
        for seat in list(detail.get("seats") or []):
            seat_id = str(seat.get("seat_id") or "")
            seat_state = dict(state_seats.get(seat_id) or {})
            seats.append(
                {
                    **dict(seat),
                    "position": str(seat_state.get("position") or ""),
                    "status": str(seat_state.get("status") or ""),
                    "hand_count": int(seat_state.get("hand_count", len(hands.get(seat_id, []))) or 0),
                    "hand": list(hands.get(seat_id, [])),
                }
            )
        events = [dict(event) for event in list(detail.get("events") or [])]
        trace_events = [event for event in events if str(event.get("event_type") or "").startswith("trace_")]
        private_events = [event for event in events if str(event.get("visibility") or "").startswith("seat:")]
        public_events = [event for event in events if str(event.get("visibility") or "") == "public"]
        return {
            **detail,
            "summary": summary,
            "seats": seats,
            "case_file": dict(hidden.get("case_file") or {}),
            "hands": hands,
            "analysis": analysis,
            "game_metrics": dict(analysis.get("game_metrics") or {}),
            "latency_targets_ms": dict(analysis.get("latency_targets_ms") or {}),
            "agent_runtime": dict(analysis.get("agent_runtime") or {}),
            "turn_metrics": list(analysis.get("turn_metrics") or []),
            "social": dict(state.get("social") or {}),
            "public_events": public_events,
            "private_events": private_events,
            "trace_events": trace_events,
        }

    def admin_nhp_history(self, *, agent_identity: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Return saved-game history for non-human player identities."""

        return self._repository.list_nhp_history(agent_identity=agent_identity, limit=limit)

    def admin_human_history(self, *, player_identity: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Return saved-game history for normalized human display names."""

        return self._repository.list_human_player_history(player_identity=player_identity, limit=limit)

    def admin_retry_nhp_memory(self, memory_ids: list[str] | None = None) -> dict[str, Any]:
        """Retry pending or failed durable NHP memory jobs for Administrator Mode."""

        if memory_ids:
            jobs = [
                job
                for memory_id in memory_ids
                if (job := self._repository.get_nhp_memory_job(str(memory_id))) is not None
            ]
        else:
            jobs = self._repository.list_pending_nhp_memory_jobs(include_failed=True, limit=100)
        results = [self._attempt_memory_job(job, allow_retry=True) for job in jobs]
        return {"attempted": len(results), "results": results}

    @staticmethod
    def _seat_configs_from_payload(requested_seats: list[dict[str, Any]], *, default_ui_mode: str = DEFAULT_UI_MODE) -> list[SeatConfig]:
        """Normalize create-game seat payloads and drop seats marked as not playing.

        ``heuristic`` remains a tolerated legacy alias in incoming payloads so old
        clients or tests can keep working, but newly created seats are normalized
        to ``llm`` at the config boundary. The deterministic heuristic policy still
        exists internally for focused tests and legacy stored state, not as an LLM
        substitute.
        """

        seat_payloads = []
        for item in requested_seats:
            seat_kind = str(item.get("seat_kind", "human")).strip().lower()
            if seat_kind == "np":
                continue
            if seat_kind == "heuristic":
                seat_kind = "llm"
            ui_mode = GameService._ui_mode_from_payload(item, default=default_ui_mode)
            seat_payloads.append(item | {"seat_kind": seat_kind or "human", "ui_mode": ui_mode})
        if len(seat_payloads) < 3 or len(seat_payloads) > 6:
            raise ValueError("Clue requires between 3 and 6 active seats.")
        return [SeatConfig.from_dict(item) for item in seat_payloads]

    @staticmethod
    def _ui_mode_from_payload(payload: dict[str, Any], *, default: str = DEFAULT_UI_MODE) -> str:
        """Normalize the create-table UI mode and reject unavailable modes."""

        raw_mode = normalize_ui_mode(payload.get("ui_mode", default), default=default)
        if raw_mode in UNAVAILABLE_UI_MODES:
            raise ValueError("Superplayer mode is not available yet.")
        if raw_mode not in LIVE_UI_MODES:
            allowed = ", ".join(sorted(LIVE_UI_MODES))
            raise ValueError(f"ui_mode must be one of: {allowed}.")
        return raw_mode

    @staticmethod
    def _mentioned_public_seat_ids(state: dict[str, Any], event: dict[str, Any]) -> list[str]:
        """Return the seat ids explicitly named in one public event's visible text."""

        text = str((event.get("payload") or {}).get("text") or event.get("message") or "").lower()
        mentions = []
        for seat_id, seat in state.get("seats", {}).items():
            display_name = str(seat.get("display_name") or "").lower()
            character = str(seat.get("character") or "").lower()
            if (display_name and display_name in text) or (character and character in text):
                mentions.append(str(seat_id))
        return mentions

    def _public_event_involves_seat(self, state: dict[str, Any], event: dict[str, Any], seat_id: str) -> bool:
        """Report whether one public event directly involves the provided seat."""

        return (
            self._public_event_actor_seat_id(event) == str(seat_id)
            or str(seat_id) in self._mentioned_public_seat_ids(state, event)
        )

    @staticmethod
    def _mood_from_tone(tone: str) -> str:
        """Map one chat tone into the bounded social mood enum."""

        normalized = str(tone or "").strip().lower()
        if normalized in {"playful", "wry", "flirtatious"}:
            return "amused"
        if normalized in {"cutting"}:
            return "annoyed"
        if normalized in {"guarded"}:
            return "guarded"
        if normalized in {"confident"}:
            return "confident"
        if normalized in {"warm"}:
            return "calm"
        if normalized in {"measured", "dry"}:
            return "calm"
        return "calm"

    @staticmethod
    def _thread_kind_from_decision(decision: Any) -> str:
        """Map one chat decision intent/tone onto a bounded thread kind."""

        intent = str(getattr(decision, "intent", "") or "").strip().lower()
        tone = str(getattr(decision, "tone", "") or "").strip().lower()
        if tone == "flirtatious":
            return "flirtation"
        return {
            "challenge": "dispute",
            "reconcile": "alliance",
            "ally": "alliance",
            "tease": "banter",
            "deflect": "meta",
            "meta_observe": "meta",
        }.get(intent, "meta")

    @staticmethod
    def _social_relationship_entry(social: dict[str, Any], seat_id: str, target_seat_id: str) -> dict[str, Any]:
        """Return one mutable relationship entry, creating a neutral one if absent."""

        seat_social = dict((social.get("seats") or {}).get(seat_id) or {})
        relationships = dict(seat_social.get("relationships") or {})
        relationship = dict(relationships.get(target_seat_id) or {"affinity": 0, "trust": 0, "friction": 0})
        relationships[target_seat_id] = relationship
        seat_social["relationships"] = relationships
        social.setdefault("seats", {})[seat_id] = GameService._normalize_social_seat_state(seat_social)
        return dict((social.get("seats") or {}).get(seat_id, {}).get("relationships", {}).get(target_seat_id) or relationship)

    def _apply_relationship_deltas(
        self,
        social: dict[str, Any],
        *,
        speaker_seat_id: str,
        relationship_deltas: list[dict[str, Any]],
    ) -> None:
        """Apply bounded relationship deltas to the canonical social graph."""

        social_seats = dict(social.get("seats") or {})
        for delta in relationship_deltas:
            target_seat_id = str(delta.get("seat_id") or "").strip()
            if not target_seat_id or target_seat_id not in social_seats:
                continue
            speaker_social = dict(social_seats.get(speaker_seat_id) or {})
            speaker_relationships = dict(speaker_social.get("relationships") or {})
            speaker_relationship = dict(speaker_relationships.get(target_seat_id) or {"affinity": 0, "trust": 0, "friction": 0})
            speaker_relationship["affinity"] = self._clamp_value(
                int(speaker_relationship.get("affinity", 0)) + int(delta.get("affinity_delta") or 0),
                minimum=-2,
                maximum=2,
            )
            speaker_relationship["trust"] = self._clamp_value(
                int(speaker_relationship.get("trust", 0)) + int(delta.get("trust_delta") or 0),
                minimum=-2,
                maximum=2,
            )
            speaker_relationship["friction"] = self._clamp_value(
                int(speaker_relationship.get("friction", 0)) + int(delta.get("friction_delta") or 0),
                minimum=0,
                maximum=3,
            )
            speaker_relationships[target_seat_id] = speaker_relationship
            speaker_social["relationships"] = speaker_relationships
            social_seats[speaker_seat_id] = self._normalize_social_seat_state(speaker_social)

            target_social = dict(social_seats.get(target_seat_id) or {})
            target_relationships = dict(target_social.get("relationships") or {})
            target_relationship = dict(target_relationships.get(speaker_seat_id) or {"affinity": 0, "trust": 0, "friction": 0})
            target_relationship["affinity"] = self._clamp_value(
                int(target_relationship.get("affinity", 0)) + (1 if int(delta.get("affinity_delta") or 0) > 0 else (-1 if int(delta.get("affinity_delta") or 0) < 0 else 0)),
                minimum=-2,
                maximum=2,
            )
            target_relationship["trust"] = self._clamp_value(
                int(target_relationship.get("trust", 0)) + (1 if int(delta.get("trust_delta") or 0) > 0 else (-1 if int(delta.get("trust_delta") or 0) < 0 else 0)),
                minimum=-2,
                maximum=2,
            )
            target_relationship["friction"] = self._clamp_value(
                int(target_relationship.get("friction", 0)) + (1 if int(delta.get("friction_delta") or 0) > 0 else (-1 if int(delta.get("friction_delta") or 0) < 0 else 0)),
                minimum=0,
                maximum=3,
            )
            target_relationships[speaker_seat_id] = target_relationship
            target_social["relationships"] = target_relationships
            social_seats[target_seat_id] = self._normalize_social_seat_state(target_social)
        social["seats"] = social_seats

    @staticmethod
    def _find_social_thread(social: dict[str, Any], *, participants: set[str], topic: str) -> dict[str, Any] | None:
        """Find the current active/cooling thread for the same topic and participants."""

        normalized_topic = str(topic or "").strip().lower()
        participant_set = {str(item) for item in participants if str(item)}
        for thread in list(social.get("threads") or []):
            thread_map = dict(thread or {})
            if str(thread_map.get("status") or "") == "resolved":
                continue
            if str(thread_map.get("topic") or "").strip().lower() != normalized_topic:
                continue
            if {str(item) for item in list(thread_map.get("participants") or []) if str(item)} == participant_set:
                return thread_map
        return None

    @staticmethod
    def _hottest_social_thread(social: dict[str, Any], *, seat_id: str = "") -> dict[str, Any]:
        """Return the hottest active or cooling thread, optionally limited to one seat."""

        threads = []
        for thread in list(social.get("threads") or []):
            thread_map = dict(thread or {})
            if str(thread_map.get("status") or "") == "resolved":
                continue
            participants = [str(item) for item in list(thread_map.get("participants") or []) if str(item)]
            if seat_id and str(seat_id) not in participants:
                continue
            threads.append(thread_map)
        return max(
            threads,
            key=lambda item: (
                int(item.get("heat") or 0),
                -int(item.get("burst_count") or 0),
                int(item.get("last_event_index") or 0),
                str(item.get("thread_id") or ""),
            ),
            default={},
        )

    @staticmethod
    def _has_unresolved_friction_thread(social: dict[str, Any], seat_id: str) -> bool:
        """Report whether a seat is currently in an active or cooling dispute thread."""

        return any(
            str(thread.get("kind") or "") == "dispute"
            and str(thread.get("status") or "") != "resolved"
            and str(seat_id) in {str(item) for item in list(thread.get("participants") or [])}
            for thread in list(social.get("threads") or [])
        )

    def _apply_social_public_events(
        self,
        state: dict[str, Any],
        social: dict[str, Any],
        public_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Advance thread heat and speaking streaks from new public activity."""

        threads = [self._normalize_social_thread(item) for item in list(social.get("threads") or [])]
        social_seats = dict(social.get("seats") or {})
        for event in public_events:
            event_index = int(event.get("event_index") or 0)
            event_type = str(event.get("event_type") or "")
            actor_seat_id = self._public_event_actor_seat_id(event)
            involved = {actor_seat_id, *self._mentioned_public_seat_ids(state, event)} - {""}
            event_text = str((event.get("payload") or {}).get("text") or event.get("message") or "").lower()
            if event_type == "chat_posted":
                for seat_id, seat_social in list(social_seats.items()):
                    current = dict(seat_social or {})
                    current["speaking_streak"] = (
                        max(int(current.get("speaking_streak", 0) or 0) + 1, 1) if seat_id == actor_seat_id else 0
                    )
                    social_seats[seat_id] = self._normalize_social_seat_state(current)
            for thread in threads:
                if str(thread.get("status") or "") == "resolved":
                    continue
                participants = {str(item) for item in list(thread.get("participants") or []) if str(item)}
                topic = str(thread.get("topic") or "").strip().lower()
                related = bool(participants & involved) or bool(topic and topic in event_text)
                if related:
                    thread["last_event_index"] = max(int(thread.get("last_event_index") or 0), event_index)
                    continue
                next_heat = int(thread.get("heat") or 0) - 1
                if next_heat <= 0:
                    thread["heat"] = 0
                    thread["status"] = "resolved"
                else:
                    thread["heat"] = next_heat
            social["last_public_event_index"] = max(int(social.get("last_public_event_index") or 0), event_index)
        social["threads"] = [self._normalize_social_thread(item) for item in threads]
        social["seats"] = social_seats
        return social

    def _apply_chat_decision_to_social(
        self,
        state: dict[str, Any],
        social: dict[str, Any],
        *,
        speaker_seat_id: str,
        decision: Any,
        public_event_index: int,
        prior_public_event_index: int,
    ) -> dict[str, Any]:
        """Apply one structured chat decision to the canonical social-memory state."""

        social_seats = dict(social.get("seats") or {})
        seat_social = dict(social_seats.get(speaker_seat_id) or {})
        seat_social["last_processed_public_event_index"] = max(int(seat_social.get("last_processed_public_event_index", 0) or 0), public_event_index)
        seat_social["cooldown_events_remaining"] = 2
        seat_social["last_chat_event_index"] = public_event_index
        seat_social["mood"] = self._mood_from_tone(str(getattr(decision, "tone", "") or ""))
        seat_social["focus_seat_id"] = str(getattr(decision, "target_seat_id", "") or "")
        seat_social["speaking_streak"] = max(int(seat_social.get("speaking_streak", 0) or 0) + 1, 1)
        seat_social["recent_intents"] = [*list(seat_social.get("recent_intents") or []), str(getattr(decision, "intent", "") or "")][-4:]
        social_seats[speaker_seat_id] = self._normalize_social_seat_state(seat_social)
        social["seats"] = social_seats
        self._apply_relationship_deltas(
            social,
            speaker_seat_id=speaker_seat_id,
            relationship_deltas=[dict(item) for item in list(getattr(decision, "relationship_deltas", []) or [])],
        )

        target_seat_id = str(getattr(decision, "target_seat_id", "") or "")
        topic = str(getattr(decision, "topic", "") or "").strip()
        thread_action = str(getattr(decision, "thread_action", "") or "observe").strip().lower()
        if target_seat_id and topic and thread_action in {"open", "continue", "resolve"}:
            participants = {speaker_seat_id, target_seat_id}
            existing_thread = self._find_social_thread(social, participants=participants, topic=topic)
            thread_kind = self._thread_kind_from_decision(decision)
            thread = dict(existing_thread or {})
            if not thread:
                thread = {
                    "thread_id": str(getattr(decision, "thread_id", "") or f"thread_{speaker_seat_id}_{public_event_index}"),
                    "kind": thread_kind,
                    "topic": topic,
                    "participants": sorted(participants),
                    "heat": 1,
                    "status": "active",
                    "burst_count": 0,
                    "last_event_index": prior_public_event_index,
                }
            consecutive = int(thread.get("last_event_index") or 0) == int(prior_public_event_index)
            if thread_action == "resolve":
                thread["status"] = "cooling"
                thread["heat"] = max(int(thread.get("heat") or 1) - 1, 1)
            else:
                thread["status"] = "active"
                thread["heat"] = self._clamp_value(
                    int(thread.get("heat") or 1) + (1 if consecutive else 0),
                    minimum=1,
                    maximum=3,
                )
            thread["kind"] = thread_kind
            thread["topic"] = topic
            thread["participants"] = sorted(participants)
            thread["last_event_index"] = public_event_index
            thread["burst_count"] = self._clamp_value(
                (int(thread.get("burst_count") or 0) + 1) if consecutive and thread_action == "continue" else 0,
                minimum=0,
                maximum=2,
            )
            if int(thread.get("burst_count") or 0) >= 2:
                thread["status"] = "cooling"
            replaced = False
            threads = []
            for existing in list(social.get("threads") or []):
                existing_map = dict(existing or {})
                if str(existing_map.get("thread_id") or "") == str(thread.get("thread_id") or ""):
                    threads.append(self._normalize_social_thread(thread))
                    replaced = True
                else:
                    threads.append(self._normalize_social_thread(existing_map))
            if not replaced:
                threads.append(self._normalize_social_thread(thread))
            social["threads"] = threads
        social["last_public_event_index"] = max(int(social.get("last_public_event_index") or 0), public_event_index)
        return social

    def maybe_run_idle_chat(self, game_id: str) -> None:
        """Run at most one non-human idle-chat reply for new player-facing public activity.

        Idle chat is intentionally conservative: it never runs while an
        autonomous gameplay action is pending, it advances only on new
        player-facing public events, and it emits at most one new NPC line per
        sweep. The social-memory layer uses this method to keep table chatter
        lively without turning polling into an uncontrolled conversation loop.
        """

        state = self._repository.get_state(game_id)
        self._ensure_analysis(state)
        social = self._ensure_social(state)
        if str(state.get("status") or "") != "active":
            return
        if self._autonomous_seat_to_act(state) is not None:
            return
        if not self._idle_chat_enabled():
            return

        public_events = self._player_facing_public_events(game_id)
        if not public_events:
            return

        social_seats = dict(social.get("seats") or {})
        nonhuman_ids = [seat_id for seat_id in state.get("seat_order", []) if seat_id in social_seats]
        if not nonhuman_ids:
            return

        latest_public_event = public_events[-1]
        latest_public_event_index = int(latest_public_event["event_index"])
        all_processed = all(
            int(dict(social_seats.get(seat_id) or {}).get("last_processed_public_event_index", 0)) >= latest_public_event_index
            for seat_id in nonhuman_ids
        )

        prior_social_index = int(social.get("last_public_event_index") or 0)
        new_public_events = [event for event in public_events if int(event.get("event_index") or 0) > prior_social_index]
        state_changed = bool(new_public_events)
        if new_public_events:
            social = self._apply_social_public_events(state, social, new_public_events)

        social_seats = dict(social.get("seats") or {})
        latest_public_chat = next(
            (event for event in reversed(public_events) if str(event.get("event_type") or "") == "chat_posted"),
            {},
        )
        latest_narrative = next(
            (event for event in reversed(public_events) if str(event.get("event_type") or "") != "chat_posted"),
            {},
        )
        latest_event_actor = self._public_event_actor_seat_id(latest_public_event)
        recent_chat_authors = [
            self._public_event_actor_seat_id(event)
            for event in public_events
            if str(event.get("event_type") or "") == "chat_posted"
        ][-2:]
        hottest_thread = self._hottest_social_thread(social)
        candidates: list[dict[str, Any]] = []

        if not all_processed:
            for seat_id in nonhuman_ids:
                seat_social = dict(social_seats.get(seat_id) or {})
                last_processed = int(seat_social.get("last_processed_public_event_index", 0) or 0)
                unseen_events = [event for event in public_events if int(event.get("event_index") or 0) > last_processed]
                if not unseen_events:
                    continue

                cooldown = max(int(seat_social.get("cooldown_events_remaining", 0) or 0), 0)
                if cooldown > 0:
                    seat_social["cooldown_events_remaining"] = max(cooldown - len(unseen_events), 0)
                    seat_social["last_processed_public_event_index"] = latest_public_event_index
                    social_seats[seat_id] = self._normalize_social_seat_state(seat_social)
                    state_changed = True
                    continue

                seat = dict(state["seats"][seat_id])
                addressed = bool(latest_public_chat and self._event_mentions_seat(latest_public_chat, seat))
                participates_in_hot_thread = bool(
                    hottest_thread
                    and str(hottest_thread.get("status") or "") == "active"
                    and seat_id in {str(item) for item in list(hottest_thread.get("participants") or [])}
                    and int(hottest_thread.get("burst_count") or 0) < 2
                )
                focus_seat_id = str(seat_social.get("focus_seat_id") or "")
                focus_involved = bool(
                    focus_seat_id
                    and latest_public_event
                    and self._public_event_involves_seat(state, latest_public_event, focus_seat_id)
                )
                unresolved_friction = self._has_unresolved_friction_thread(social, seat_id)
                chance = self._idle_chat_base_chance(str(seat.get("character") or ""))
                if addressed:
                    chance += 0.20
                if participates_in_hot_thread:
                    chance += 0.15
                if focus_involved:
                    chance += 0.10
                if unresolved_friction:
                    chance += 0.10
                if seat_id in recent_chat_authors and not participates_in_hot_thread:
                    chance -= 0.25
                chance = max(0.0, min(chance, 0.90))
                roll = self._idle_chat_roll(game_id, seat_id, latest_public_event_index)
                if roll < chance:
                    candidates.append(
                        {
                            "seat_id": seat_id,
                            "addressed": addressed,
                            "latest_event_actor": latest_event_actor == seat_id,
                            "margin": round(chance - roll, 6),
                            "proactive": False,
                        }
                    )
                seat_social["last_processed_public_event_index"] = latest_public_event_index
                social_seats[seat_id] = self._normalize_social_seat_state(seat_social)
                state_changed = True
        elif (
            self._proactive_chat_enabled()
            and str(latest_public_event.get("event_type") or "") != "chat_posted"
            and int(social.get("last_proactive_turn_index", -1) or -1) < int(state.get("turn_index") or 0)
        ):
            social["last_proactive_turn_index"] = int(state.get("turn_index") or 0)
            state_changed = True
            multiplier = self._proactive_chat_chance_multiplier()
            for seat_id in nonhuman_ids:
                if seat_id in recent_chat_authors:
                    continue
                seat_social = dict(social_seats.get(seat_id) or {})
                if max(int(seat_social.get("cooldown_events_remaining", 0) or 0), 0) > 0:
                    continue
                seat = dict(state["seats"][seat_id])
                participates_in_hot_thread = bool(
                    hottest_thread
                    and str(hottest_thread.get("status") or "") == "active"
                    and seat_id in {str(item) for item in list(hottest_thread.get("participants") or [])}
                    and int(hottest_thread.get("burst_count") or 0) < 2
                )
                chance = self._idle_chat_base_chance(str(seat.get("character") or "")) * multiplier
                if participates_in_hot_thread:
                    chance += 0.08
                chance = max(0.0, min(chance, 0.35))
                roll = self._idle_chat_roll(game_id, seat_id, latest_public_event_index + int(state.get("turn_index") or 0) + 9973)
                if roll < chance:
                    candidates.append(
                        {
                            "seat_id": seat_id,
                            "addressed": False,
                            "latest_event_actor": latest_event_actor == seat_id,
                            "margin": round(chance - roll, 6),
                            "proactive": True,
                        }
                    )

        social["seats"] = social_seats
        state["social"] = social
        events: list[dict[str, Any]] = []

        if candidates:
            chosen = sorted(
                candidates,
                key=lambda item: (
                    not bool(item["addressed"]),
                    not bool(item["latest_event_actor"]),
                    -float(item["margin"]),
                    state["seat_order"].index(str(item["seat_id"])),
                ),
            )[0]
            seat_id = str(chosen["seat_id"])
            seat_row = next(item for item in self._repository.list_seats(game_id) if item["seat_id"] == seat_id)
            seat = dict(seat_row) | dict(state["seats"][seat_id]) | {"seat_id": seat_id, "notebook": seat_row["notebook"]}
            snapshot = self._build_internal_snapshot(game_id, seat_id)
            try:
                decision = self._agents.decide_chat(seat=seat, snapshot=snapshot)
            except LLMDecisionError as exc:
                if (exc.debug or {}).get("tool_writes"):
                    state = self._merge_latest_social_state(state)
                events.append(
                    self._trace_event(
                        "trace_llm_unavailable",
                        message=f"{seat['display_name']}'s LLM chat was unavailable and no heuristic chat was posted.",
                        payload={
                            "seat_id": seat_id,
                            "seat_kind": seat.get("seat_kind", ""),
                            "mode": exc.mode,
                            "reason": exc.reason,
                            "runtime": dict(exc.runtime or {}),
                            "debug": dict(exc.debug or {}),
                            "error": str(exc.error or ""),
                        },
                        visibility=f"seat:{seat_id}",
                    )
                )
                state_changed = True
                decision = None
            if decision is None:
                pass
            else:
                if decision.agent_meta.get("tool_writes"):
                    state = self._merge_latest_social_state(state)
                    state_changed = True
                safe_text = sanitize_public_chat(str(decision.text or ""))
                if decision.speak and safe_text:
                    next_public_index = self._repository.next_event_index(game_id) + 1
                    chat_game = GameMaster(state)
                    state, events = chat_game.apply_action(seat_id, {"action": "send_chat", "text": safe_text})
                    updated_social = self._ensure_social(state)
                    state["social"] = self._apply_chat_decision_to_social(
                        state,
                        updated_social,
                        speaker_seat_id=seat_id,
                        decision=decision,
                        public_event_index=next_public_index,
                        prior_public_event_index=latest_public_event_index,
                    )
                    state_changed = True

        if state_changed:
            self._repository.save_state_and_events(game_id, state=state, events=events)

    def maybe_run_agents(self, game_id: str, *, max_cycles: int = 32) -> None:
        """Advance queued heuristic/LLM turns until a human seat must respond.

        Each cycle rebuilds the latest seat-private snapshot, refreshes the
        deduction helper payload, asks the selected seat agent for one decision,
        and then pushes that decision back through ``GameMaster``. The loop stops
        as soon as the game completes, a human response is required, or the
        cycle cap is reached.
        """

        cycles = 0
        while cycles < max_cycles:
            state = self._repository.get_state(game_id)
            self._ensure_analysis(state)
            self._ensure_social(state)
            if state["status"] != "active":
                self._finalize_game_if_complete(game_id, state)
                return
            seat_id = self._autonomous_seat_to_act(state)
            if seat_id is None:
                return
            seat = next(item for item in self._repository.list_seats(game_id) if item["seat_id"] == seat_id)
            seat = dict(seat) | dict(state["seats"][seat_id]) | {"seat_id": seat_id, "notebook": seat["notebook"]}
            cycle_started = time.perf_counter()
            snapshot = self._build_internal_snapshot(game_id, seat_id)
            tool_snapshot = self._tool_snapshot_for(state, seat_id, snapshot["events"])
            tool_snapshot_payload = asdict(tool_snapshot)
            decision_started = time.perf_counter()
            try:
                decision = self._agents.decide(seat=seat, snapshot=snapshot, tool_snapshot=tool_snapshot_payload)
            except LLMDecisionError as exc:
                state = self._merge_latest_social_state(state)
                decision_latency_ms = round((time.perf_counter() - decision_started) * 1000.0, 2)
                metric = {
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "turn_index": int(state["turn_index"]),
                    "seat_id": seat_id,
                    "seat_kind": str(seat["seat_kind"]),
                    "actor": str(seat["seat_kind"]),
                    "action": "llm_unavailable",
                    "latency_ms": round((time.perf_counter() - cycle_started) * 1000.0, 2),
                    "tool_snapshot_latency_ms": float(tool_snapshot.generation.get("elapsed_ms", 0.0)),
                    "agent_decision_latency_ms": decision_latency_ms,
                    "fallback_used": False,
                    "fallback_reason": "",
                    "llm_error_reason": exc.reason,
                    "rejection_kind": "llm_unavailable",
                    "trace_id": "",
                    "session_id": "",
                    "last_response_id": "",
                    "tool_call_count": 0,
                    "reasoning_effort": str((exc.runtime or {}).get("reasoning_effort", "")),
                    "model": str((exc.runtime or {}).get("default_model", "")),
                    "guardrail_blocks": int(exc.reason in {"output_guardrail_blocked", "input_guardrail_blocked", "unsafe_public_chat"}),
                    "sampling_timed_out": bool(
                        exc.reason == "timeout" or tool_snapshot.generation.get("sampling_timed_out")
                    ),
                    "latency_budget_breached": False,
                    "rejected": True,
                    "error": str(exc),
                }
                private_debug = {
                    "recorded_at": metric["recorded_at"],
                    "decision": {
                        "action": "llm_unavailable",
                        "rationale_private": "The live LLM path failed; no heuristic turn was generated.",
                        "agent_meta": {
                            "policy": "llm",
                            "fallback_used": False,
                            "llm_error_reason": exc.reason,
                        },
                    },
                    "tool_snapshot": {
                        "belief_summary": dict(tool_snapshot.belief_summary or {}),
                        "top_hypotheses": list(tool_snapshot.top_hypotheses or [])[:3],
                        "suggestion_ranking": list(tool_snapshot.suggestion_ranking or [])[:3],
                        "accusation": dict(tool_snapshot.accusation or {}),
                        "opponent_model": dict(tool_snapshot.opponent_model or {}),
                        "generation": dict(tool_snapshot.generation or {}),
                    },
                    "decision_debug": {
                        "llm_runtime": dict(exc.runtime or {}),
                        "llm_debug": dict(exc.debug or {}),
                        "error": str(exc.error or ""),
                    },
                    "metric": metric,
                }
                self._record_turn_metric(state, metric, private_debug=private_debug)
                self._repository.save_state_and_events(
                    game_id,
                    state=state,
                    events=[
                        make_event(
                            "llm_unavailable",
                            message=f"{seat['display_name']}'s LLM seat could not act ({exc.reason}); no heuristic move was used.",
                            payload={
                                "seat_id": seat_id,
                                "seat_kind": seat["seat_kind"],
                                "reason": exc.reason,
                                "mode": exc.mode,
                            },
                        ),
                        self._trace_event(
                            "trace_llm_unavailable",
                            message=f"Private LLM failure trace recorded for {seat['display_name']}.",
                            payload=private_debug,
                            visibility=f"seat:{seat_id}",
                        ),
                    ],
                )
                return
            decision_latency_ms = round((time.perf_counter() - decision_started) * 1000.0, 2)
            state = self._merge_latest_social_state(state)
            game = GameMaster(state)
            try:
                new_state, events = game.apply_action(seat_id, decision.to_action_payload())
            except ValueError as exc:
                rejected_metric = {
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "turn_index": int(state["turn_index"]),
                    "seat_id": seat_id,
                    "seat_kind": str(seat["seat_kind"]),
                    "actor": str(seat["seat_kind"]),
                    "action": str(decision.action),
                    "latency_ms": round((time.perf_counter() - cycle_started) * 1000.0, 2),
                    "tool_snapshot_latency_ms": float(tool_snapshot.generation.get("elapsed_ms", 0.0)),
                    "agent_decision_latency_ms": decision_latency_ms,
                    "fallback_used": bool(decision.agent_meta.get("fallback_used")),
                    "fallback_reason": str(decision.agent_meta.get("fallback_reason", "")),
                    "trace_id": str(decision.agent_meta.get("trace_id", "")),
                    "session_id": str(decision.agent_meta.get("session_id", "")),
                    "last_response_id": str(decision.agent_meta.get("last_response_id", "")),
                    "tool_call_count": int(decision.agent_meta.get("tool_call_count", 0) or 0),
                    "tool_writes": list(decision.agent_meta.get("tool_writes") or []),
                    "reasoning_effort": str(decision.agent_meta.get("reasoning_effort", "")),
                    "model": str(decision.agent_meta.get("model", "")),
                    "guardrail_blocks": 0,
                    "sampling_timed_out": bool(tool_snapshot.generation.get("sampling_timed_out")),
                    "latency_budget_breached": False,
                    "rejected": True,
                    "error": str(exc),
                }
                self._record_turn_metric(state, rejected_metric)
                self._repository.save_state_and_events(
                    game_id,
                    state=state,
                    events=[
                        self._trace_event(
                            "trace_action_rejected",
                            message=f"Autonomous action from {seat['display_name']} was rejected and logged.",
                            payload=rejected_metric,
                            visibility=f"seat:{seat_id}",
                        )
                    ],
                )
                self._finalize_game_if_complete(game_id, state)
                return
            guardrail_blocks = 0
            if decision.text:
                safe_chat = sanitize_public_chat(decision.text)
                if safe_chat:
                    chat_game = GameMaster(new_state)
                    new_state, chat_events = chat_game.apply_action(seat_id, {"action": "send_chat", "text": safe_chat})
                    events.extend(chat_events)
                if safe_chat != decision.text:
                    guardrail_blocks = 1
                    events.append(
                        self._trace_event(
                            "trace_guardrail_blocked",
                            message=f"Public chat from {seat['display_name']} was sanitized before posting.",
                            payload={"seat_id": seat_id, "actor": seat["seat_kind"]},
                        )
                    )
            metric = {
                "recorded_at": datetime.now(UTC).isoformat(),
                "turn_index": int(new_state["turn_index"]),
                "seat_id": seat_id,
                "seat_kind": str(seat["seat_kind"]),
                "actor": str(seat["seat_kind"]),
                "action": str(decision.action),
                "latency_ms": round((time.perf_counter() - cycle_started) * 1000.0, 2),
                "tool_snapshot_latency_ms": float(tool_snapshot.generation.get("elapsed_ms", 0.0)),
                "agent_decision_latency_ms": decision_latency_ms,
                "fallback_used": bool(decision.agent_meta.get("fallback_used")),
                "fallback_reason": str(decision.agent_meta.get("fallback_reason", "")),
                "trace_id": str(decision.agent_meta.get("trace_id", "")),
                "session_id": str(decision.agent_meta.get("session_id", "")),
                "last_response_id": str(decision.agent_meta.get("last_response_id", "")),
                "tool_call_count": int(decision.agent_meta.get("tool_call_count", 0) or 0),
                "tool_calls": list(decision.agent_meta.get("tool_calls") or []),
                "tool_writes": list(decision.agent_meta.get("tool_writes") or []),
                "reasoning_effort": str(decision.agent_meta.get("reasoning_effort", "")),
                "model": str(decision.agent_meta.get("model", "")),
                "guardrail_blocks": guardrail_blocks + int(bool(decision.agent_meta.get("guardrail_blocked"))),
                "sampling_timed_out": bool(tool_snapshot.generation.get("sampling_timed_out")),
                "latency_budget_breached": bool(
                    float(tool_snapshot.generation.get("elapsed_ms", 0.0))
                    > float(state["analysis"]["latency_targets_ms"]["tool_snapshot_ms"])
                    or decision_latency_ms > float(state["analysis"]["latency_targets_ms"]["llm_turn_ms"])
                ),
                "rejected": False,
                "sample_count": int(tool_snapshot.sample_count),
                "accusation_correct": bool(decision.action == "accuse" and new_state.get("winner_seat_id") == seat_id),
            }
            private_debug = {
                "recorded_at": metric["recorded_at"],
                "decision": {
                    "action": decision.action,
                    "rationale_private": decision.rationale_private,
                    "agent_meta": dict(decision.agent_meta or {}),
                },
                "tool_snapshot": {
                    "belief_summary": dict(tool_snapshot.belief_summary or {}),
                    "top_hypotheses": list(tool_snapshot.top_hypotheses or [])[:3],
                    "suggestion_ranking": list(tool_snapshot.suggestion_ranking or [])[:3],
                    "accusation": dict(tool_snapshot.accusation or {}),
                    "opponent_model": dict(tool_snapshot.opponent_model or {}),
                    "generation": dict(tool_snapshot.generation or {}),
                },
                "decision_debug": dict(decision.debug_private or {}),
                "metric": metric,
            }
            self._record_turn_metric(new_state, metric, private_debug=private_debug)
            events.extend(
                [
                    self._trace_event(
                        "trace_tool_snapshot",
                        message=f"Private tool snapshot recorded for {seat['display_name']}.",
                        payload=private_debug["tool_snapshot"],
                        visibility=f"seat:{seat_id}",
                    ),
                    self._trace_event(
                        "trace_seat_decision",
                        message=f"Private decision trace recorded for {seat['display_name']}.",
                        payload=private_debug["decision"] | {"decision_debug": private_debug["decision_debug"]},
                        visibility=f"seat:{seat_id}",
                    ),
                    self._trace_event(
                        "trace_turn_metric",
                        message=f"Private turn metric recorded for {seat['display_name']}.",
                        payload=metric,
                        visibility=f"seat:{seat_id}",
                    ),
                    self._trace_event(
                        "trace_worker_cycle",
                        message=f"{seat['display_name']} resolved {decision.action} in {metric['latency_ms']} ms.",
                        payload={
                            "seat_id": seat_id,
                            "seat_kind": seat["seat_kind"],
                            "action": decision.action,
                            "latency_ms": metric["latency_ms"],
                            "fallback_used": metric["fallback_used"],
                        },
                    ),
                ]
            )
            self._repository.save_state_and_events(game_id, state=new_state, events=events)
            self._finalize_game_if_complete(game_id, new_state)
            cycles += 1

    def _player_facing_public_events(self, game_id: str, *, since_event_index: int = 0) -> list[dict[str, Any]]:
        """Return public events intended for player-facing narrative or chat feeds."""

        return [
            dict(event)
            for event in self._repository.public_events(game_id, since_event_index=since_event_index)
            if not str(event.get("event_type") or "").startswith("trace_")
        ]

    @staticmethod
    def _public_event_actor_seat_id(event: dict[str, Any]) -> str:
        """Best-effort extraction of the acting seat id from one public event."""

        payload = dict(event.get("payload") or {})
        for key in ("seat_id", "from_seat_id", "winner_seat_id", "suggester"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _event_mentions_seat(event: dict[str, Any], seat: dict[str, Any]) -> bool:
        """Report whether one public chat line names the provided seat."""

        text = str((event.get("payload") or {}).get("text") or event.get("message") or "").lower()
        display_name = str(seat.get("display_name") or "").lower()
        character = str(seat.get("character") or "").lower()
        return bool(text and ((display_name and display_name in text) or (character and character in text)))

    @staticmethod
    def _idle_chat_base_chance(character: str) -> float:
        """Map the persona chattiness slider onto a base chat probability."""

        return {
            1: 0.08,
            2: 0.18,
            3: 0.32,
            4: 0.50,
            5: 0.68,
        }.get(persona_chattiness(character), 0.32)

    def _idle_chat_enabled(self) -> bool:
        """Return whether snapshot-triggered optional NHP chat may run."""

        return bool(self.admin_runtime_settings()["effective"]["idle_chat_enabled"])

    def _proactive_chat_enabled(self) -> bool:
        """Return whether quiet-table proactive NHP chat is enabled."""

        return bool(self.admin_runtime_settings()["effective"]["proactive_chat_enabled"])

    def _proactive_chat_chance_multiplier(self) -> float:
        """Return the bounded multiplier applied to quiet-table chat chance."""

        return float(self.admin_runtime_settings()["effective"]["proactive_chat_chance_multiplier"])

    @staticmethod
    def _idle_chat_roll(game_id: str, seat_id: str, latest_public_event_index: int) -> float:
        """Return a deterministic 0..1 roll for one idle-chat evaluation."""

        material = f"{str(game_id).strip()}|{str(seat_id).strip()}|{int(latest_public_event_index)}".encode("utf-8")
        value = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        return value / float(2**64)

    def _tool_snapshot_for(self, state: dict[str, Any], seat_id: str, visible_events: list[dict[str, Any]]):
        """Build the deduction helper payload for one autonomous seat decision.

        The returned snapshot is shared between heuristic and LLM seats so both
        policy paths reason over the same seat-local belief state.
        """

        hand_counts = {other_id: int(seat["hand_count"]) for other_id, seat in state["seats"].items()}
        room_name = None
        seat_position = str(state["seats"][seat_id]["position"])
        if seat_position in state.get("hidden", {}):
            room_name = None
        snapshot = GameMaster(state)
        room_name = snapshot.current_room(seat_id)
        return build_tool_snapshot(
            seat_id=seat_id,
            seat_hand=list(state["hidden"]["hands"][seat_id]),
            hand_counts=hand_counts,
            visible_events=visible_events,
            room_name=room_name,
            time_budget_ms=self._latency_targets()["tool_snapshot_ms"],
        )

    def _build_internal_snapshot(self, game_id: str, seat_id: str) -> dict[str, Any]:
        """Build the full seat-private snapshot used internally by autonomous seats.

        This reuses the same filtered snapshot builder used by the browser so the
        model-facing path cannot accidentally see data that the server-side
        privacy boundary would otherwise hide.
        """

        state = self._repository.get_state(game_id)
        self._ensure_analysis(state)
        self._ensure_social(state)
        seat_row = next(item for item in self._repository.list_seats(game_id) if item["seat_id"] == seat_id)
        visible_events = self._repository.visible_events(game_id, seat_id=seat_id, since_event_index=0)
        snapshot = build_filtered_snapshot(state, seat_id=seat_id, visible_events=visible_events, notebook=seat_row["notebook"])
        snapshot["memory_context"] = self._memory_context_for_seat(state, seat_id)
        return snapshot

    @staticmethod
    def _apply_llm_profiles(game_id: str, seat_configs: list[SeatConfig]) -> None:
        """Assign deterministic turn and chat profiles to each LLM seat when unspecified."""

        assignments = assign_model_profiles(game_id=game_id, seats=seat_configs)
        chat_assignments = assign_chat_model_profiles(game_id=game_id, seats=seat_configs)
        for seat in seat_configs:
            selection = assignments.get(seat.seat_id)
            if selection is None:
                pass
            else:
                seat.agent_profile = selection.profile_id
                if not seat.agent_model:
                    seat.agent_model = selection.model
            chat_selection = chat_assignments.get(seat.seat_id)
            if chat_selection is None:
                continue
            seat.agent_chat_profile = chat_selection.profile_id
            if not seat.agent_chat_model:
                seat.agent_chat_model = chat_selection.model

    @staticmethod
    def _autonomous_seat_to_act(state: dict[str, Any]) -> str | None:
        """Return the non-human seat that should act next, if any."""

        pending_refute = state.get("pending_refute")
        if pending_refute:
            current_refuter = str(pending_refute["current_refuter"])
            if str(state["seats"][current_refuter]["seat_kind"]) != "human":
                return current_refuter
            return None
        active_seat_id = str(state["active_seat_id"])
        if str(state["seats"][active_seat_id]["seat_kind"]) != "human":
            return active_seat_id
        return None

    @staticmethod
    def _default_seats(ui_mode: str = DEFAULT_UI_MODE) -> list[SeatConfig]:
        """Return the default mixed-seat table used when no explicit payload is supplied."""

        defaults = [
            ("seat_scarlet", "Miss Scarlet", "Miss Scarlet", "human"),
            ("seat_mustard", "Colonel Mustard", "Colonel Mustard", "llm"),
            ("seat_peacock", "Mrs. Peacock", "Mrs. Peacock", "llm"),
            ("seat_plum", "Professor Plum", "Professor Plum", "human"),
        ]
        return [
            SeatConfig(seat_id=seat_id, display_name=display_name, character=character, seat_kind=seat_kind, ui_mode=ui_mode)
            for seat_id, display_name, character, seat_kind in defaults
        ]
