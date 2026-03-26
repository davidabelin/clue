"""Standalone Clue runtime service: create games, resolve tokens, and auto-run seats."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import secrets
from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer

from clue_agents import AgentRuntime
from clue_agents.safety import sanitize_public_chat
from clue_core.deduction import build_tool_snapshot
from clue_core.engine import GameMaster, build_filtered_snapshot
from clue_core.events import make_event
from clue_core.setup import build_hidden_setup, build_initial_state
from clue_core.types import SeatConfig
from clue_storage import ClueRepository


DEFAULT_GAME_SEED = 17


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")


class GameService:
    def __init__(self, repository: ClueRepository, *, secret_key: str) -> None:
        self._repository = repository
        self._serializer = URLSafeSerializer(secret_key, salt="clue-seat-token")
        self._agents = AgentRuntime()

    def create_game(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title", "")).strip() or "Clue Table"
        requested_seats = list(payload.get("seats") or [])
        if requested_seats:
            seat_configs = self._seat_configs_from_payload(requested_seats)
        else:
            seat_configs = self._default_seats()
        game_id = f"clue_{_timestamp_slug()}"
        seed = DEFAULT_GAME_SEED
        hidden_setup = build_hidden_setup(seat_configs, seed=seed)
        state = build_initial_state(game_id, title, seat_configs, hidden_setup)
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
                    "url": f"/join/{token}",
                }
            )
        initial_events = [
            make_event("game_created", payload={"game_id": game_id, "title": title}, message=f"{title} was created."),
            make_event(
                "turn_started",
                payload={"seat_id": state["active_seat_id"], "turn_index": state["turn_index"]},
                message=f"It is now {state['seats'][state['active_seat_id']]['display_name']}'s turn.",
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
        seat = self.resolve_token(token)
        self._repository.mark_seat_seen(seat["game_id"], seat["seat_id"])
        return seat

    def resolve_token(self, token: str) -> dict[str, Any]:
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
        seat = self.resolve_token(token)
        state = self._repository.get_state(seat["game_id"])
        game = GameMaster(state)
        new_state, events = game.apply_action(seat["seat_id"], action)
        public_chat = sanitize_public_chat(str(action.get("text", "")).strip()) if action.get("action") != "send_chat" else ""
        if public_chat:
            chat_game = GameMaster(new_state)
            new_state, chat_events = chat_game.apply_action(seat["seat_id"], {"action": "send_chat", "text": public_chat})
            events.extend(chat_events)
        self._repository.save_state_and_events(seat["game_id"], state=new_state, events=events)
        self.maybe_run_agents(seat["game_id"])
        return self.snapshot_for_token(token)

    def update_notebook(self, token: str, notebook: dict[str, Any]) -> dict[str, Any]:
        seat = self.resolve_token(token)
        self._repository.update_notebook(seat["game_id"], seat["seat_id"], notebook)
        return self.snapshot_for_token(token)

    @staticmethod
    def _seat_configs_from_payload(requested_seats: list[dict[str, Any]]) -> list[SeatConfig]:
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
        cycles = 0
        while cycles < max_cycles:
            state = self._repository.get_state(game_id)
            if state["status"] != "active":
                return
            seat_id = self._autonomous_seat_to_act(state)
            if seat_id is None:
                return
            seat = next(item for item in self._repository.list_seats(game_id) if item["seat_id"] == seat_id)
            snapshot = self._build_internal_snapshot(game_id, seat_id)
            tool_snapshot = self._tool_snapshot_for(state, seat_id, snapshot["events"])
            decision = self._agents.decide(seat=seat, snapshot=snapshot, tool_snapshot=asdict(tool_snapshot))
            game = GameMaster(state)
            new_state, events = game.apply_action(seat_id, decision.to_action_payload())
            if decision.text:
                safe_chat = sanitize_public_chat(decision.text)
                if safe_chat:
                    chat_game = GameMaster(new_state)
                    new_state, chat_events = chat_game.apply_action(seat_id, {"action": "send_chat", "text": safe_chat})
                    events.extend(chat_events)
            self._repository.save_state_and_events(game_id, state=new_state, events=events)
            cycles += 1

    def _tool_snapshot_for(self, state: dict[str, Any], seat_id: str, visible_events: list[dict[str, Any]]):
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
        )

    def _build_internal_snapshot(self, game_id: str, seat_id: str) -> dict[str, Any]:
        state = self._repository.get_state(game_id)
        seat_row = next(item for item in self._repository.list_seats(game_id) if item["seat_id"] == seat_id)
        visible_events = self._repository.visible_events(game_id, seat_id=seat_id, since_event_index=0)
        return build_filtered_snapshot(state, seat_id=seat_id, visible_events=visible_events, notebook=seat_row["notebook"])

    @staticmethod
    def _autonomous_seat_to_act(state: dict[str, Any]) -> str | None:
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
