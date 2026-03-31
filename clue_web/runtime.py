"""Standalone Clue runtime service: create games, resolve tokens, and auto-run seats."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import os
import secrets
import time
from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer

from clue_agents import AgentRuntime
from clue_agents.profile_loader import assign_model_profiles
from clue_agents.safety import sanitize_public_chat
from clue_core.deduction import build_tool_snapshot
from clue_core.engine import GameMaster, build_filtered_snapshot
from clue_core.events import make_event
from clue_core.setup import build_hidden_setup, build_initial_state
from clue_core.types import SeatConfig
from clue_core.version import CLUE_RELEASE_LABEL, CLUE_VERSION
from clue_storage import ClueRepository


DEFAULT_GAME_SEED = 17
DEFAULT_TOOL_SNAPSHOT_BUDGET_MS = 250
TURN_METRIC_LIMIT = 256


def _timestamp_slug() -> str:
    """Generate sortable timestamp ids for new game records."""

    return datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")


class GameService:
    """High-level gameplay service that bridges web requests, storage, and agents."""

    def __init__(self, repository: ClueRepository, *, secret_key: str) -> None:
        """Store repository access and build the seat-token serializer."""

        self._repository = repository
        self._serializer = URLSafeSerializer(secret_key, salt="clue-seat-token")
        self._agents = AgentRuntime()

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
        """Create the persisted analysis block used for traces, evals, and diagnostics."""

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
        """Create one new game, persist it, and immediately run any autonomous seats."""

        title = str(payload.get("title", "")).strip() or "Clue Table"
        requested_seats = list(payload.get("seats") or [])
        if requested_seats:
            seat_configs = self._seat_configs_from_payload(requested_seats)
        else:
            seat_configs = self._default_seats()
        game_id = f"clue_{_timestamp_slug()}"
        self._apply_llm_profiles(game_id, seat_configs)
        seed = DEFAULT_GAME_SEED
        hidden_setup = build_hidden_setup(seat_configs, seed=seed)
        state = build_initial_state(game_id, title, seat_configs, hidden_setup)
        state["analysis"] = self._build_analysis_defaults(state)
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
        """Return the public/private snapshot visible to one seat token."""

        seat = self.resolve_token(token)
        state = self._repository.get_state(seat["game_id"])
        visible_events = self._repository.visible_events(seat["game_id"], seat_id=seat["seat_id"], since_event_index=since_event_index)
        return build_filtered_snapshot(
            state,
            seat_id=seat["seat_id"],
            visible_events=visible_events,
            notebook=seat["notebook"],
        )

    def submit_action(self, token: str, action: dict[str, Any]) -> dict[str, Any]:
        """Apply one human-seat action, then continue any autonomous follow-up turns."""

        seat = self.resolve_token(token)
        state = self._repository.get_state(seat["game_id"])
        self._ensure_analysis(state)
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
            self._cleanup_llm_sessions_if_complete(state)
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
        self._cleanup_llm_sessions_if_complete(new_state)
        self.maybe_run_agents(seat["game_id"])
        return self.snapshot_for_token(token)

    def update_notebook(self, token: str, notebook: dict[str, Any]) -> dict[str, Any]:
        """Persist a seat notebook change and return the refreshed filtered snapshot."""

        seat = self.resolve_token(token)
        self._repository.update_notebook(seat["game_id"], seat["seat_id"], notebook)
        return self.snapshot_for_token(token)

    @staticmethod
    def _seat_configs_from_payload(requested_seats: list[dict[str, Any]]) -> list[SeatConfig]:
        """Normalize create-game seat payloads and drop seats marked as not playing."""

        seat_payloads = []
        for item in requested_seats:
            seat_kind = str(item.get("seat_kind", "human")).strip().lower()
            if seat_kind == "np":
                continue
            seat_payloads.append(item | {"seat_kind": seat_kind or "human"})
        if len(seat_payloads) < 3 or len(seat_payloads) > 6:
            raise ValueError("Clue requires between 3 and 6 active seats.")
        return [SeatConfig.from_dict(item) for item in seat_payloads]

    def maybe_run_agents(self, game_id: str, *, max_cycles: int = 32) -> None:
        """Advance queued heuristic/LLM turns until a human seat must respond."""

        cycles = 0
        while cycles < max_cycles:
            state = self._repository.get_state(game_id)
            self._ensure_analysis(state)
            if state["status"] != "active":
                self._cleanup_llm_sessions_if_complete(state)
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
            decision = self._agents.decide(seat=seat, snapshot=snapshot, tool_snapshot=tool_snapshot_payload)
            decision_latency_ms = round((time.perf_counter() - decision_started) * 1000.0, 2)
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
                self._cleanup_llm_sessions_if_complete(state)
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
            self._cleanup_llm_sessions_if_complete(new_state)
            cycles += 1

    def _tool_snapshot_for(self, state: dict[str, Any], seat_id: str, visible_events: list[dict[str, Any]]):
        """Build the deduction helper payload for one autonomous seat decision."""

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
        """Build the full seat-private snapshot used internally by autonomous seats."""

        state = self._repository.get_state(game_id)
        seat_row = next(item for item in self._repository.list_seats(game_id) if item["seat_id"] == seat_id)
        visible_events = self._repository.visible_events(game_id, seat_id=seat_id, since_event_index=0)
        return build_filtered_snapshot(state, seat_id=seat_id, visible_events=visible_events, notebook=seat_row["notebook"])

    @staticmethod
    def _apply_llm_profiles(game_id: str, seat_configs: list[SeatConfig]) -> None:
        """Assign one deterministic model profile to each LLM seat when unspecified."""

        assignments = assign_model_profiles(game_id=game_id, seats=seat_configs)
        for seat in seat_configs:
            selection = assignments.get(seat.seat_id)
            if selection is None:
                continue
            seat.agent_profile = selection.profile_id
            if not seat.agent_model:
                seat.agent_model = selection.model

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
    def _default_seats() -> list[SeatConfig]:
        """Return the default mixed-seat table used when no explicit payload is supplied."""

        defaults = [
            ("seat_scarlet", "Miss Scarlet", "Miss Scarlet", "human"),
            ("seat_mustard", "Colonel Mustard", "Colonel Mustard", "heuristic"),
            ("seat_peacock", "Mrs. Peacock", "Mrs. Peacock", "llm"),
            ("seat_plum", "Professor Plum", "Professor Plum", "heuristic"),
        ]
        return [
            SeatConfig(seat_id=seat_id, display_name=display_name, character=character, seat_kind=seat_kind)
            for seat_id, display_name, character, seat_kind in defaults
        ]
