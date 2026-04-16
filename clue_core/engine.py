"""Deterministic Clue rules engine and seat snapshot builder.

The rules engine owns gameplay legality and nothing else. It does not know about
Flask requests, storage, prompts, sessions, or browser concerns. That separation
is deliberate: if a change affects actual turn outcomes, it should be visible
and testable here first.
"""

from __future__ import annotations

from copy import deepcopy
import random
from typing import Any

from clue_core.board import (
    BOARD_NODES,
    GRAPH,
    NODE_TO_ROOM_NAME,
    ROOM_NAME_TO_NODE,
    SECRET_PASSAGES,
    is_room,
    reachable_nodes,
)
from clue_core.constants import CARD_CATEGORIES, ROOMS, SUSPECTS, WEAPONS
from clue_core.events import make_event
from clue_core.types import DEFAULT_UI_MODE, LIVE_UI_MODES, normalize_ui_mode


Action = dict[str, Any]


class GameMaster:
    """Apply validated actions to one Clue game state.

    ``GameMaster`` works against an in-memory mutable copy of persisted state and
    emits event rows describing what happened. Callers are responsible for
    persistence and any higher-level orchestration, but this class remains the
    sole authority on what is legal within classic Clue flow.
    """

    def __init__(self, state: dict[str, Any], rng: random.Random | None = None) -> None:
        """Copy one persisted state snapshot into a mutable rules-engine instance."""

        self.state = deepcopy(state)
        self._rng = rng or random.Random()

    @property
    def active_seat_id(self) -> str:
        """Return the seat id whose turn or refutation window is currently active."""

        return str(self.state["active_seat_id"])

    def seat(self, seat_id: str) -> dict[str, Any]:
        """Return the public seat record for one seat id."""

        return self.state["seats"][seat_id]

    def current_room(self, seat_id: str) -> str | None:
        """Return the room name for a seat, or ``None`` when not in a room."""

        node_id = str(self.seat(seat_id)["position"])
        return NODE_TO_ROOM_NAME.get(node_id)

    def hidden_hand(self, seat_id: str) -> list[str]:
        """Return the private hand for one seat from the hidden state block."""

        return list(self.state["hidden"]["hands"][seat_id])

    def case_file(self) -> dict[str, str]:
        """Return the hidden solution envelope."""

        return dict(self.state["hidden"]["case_file"])

    def occupied_hallways(self, *, exclude_seat: str | None = None) -> set[str]:
        """Collect hallway nodes that are blocked by other seat positions."""

        blocked = set()
        for seat_id, seat in self.state["seats"].items():
            if exclude_seat is not None and seat_id == exclude_seat:
                continue
            node_id = str(seat["position"])
            if BOARD_NODES[node_id]["kind"] == "hallway":
                blocked.add(node_id)
        return blocked

    def legal_actions(self, seat_id: str) -> dict[str, Any]:
        """Compute the current legal-action envelope for one seat-local snapshot.

        This envelope is the shared contract used by the browser, heuristic
        seats, and LLM guardrails. If it changes, review all three callers.
        """

        if self.state["status"] != "active":
            return {"available": []}

        pending_refute = self.state.get("pending_refute")
        if pending_refute:
            current_refuter = str(pending_refute["current_refuter"])
            if seat_id != current_refuter:
                return {"available": ["send_chat"]}
            options = sorted(self._refute_cards_for_seat(seat_id, pending_refute["suggestion"]))
            return {
                "available": (["show_refute_card", "send_chat"] if options else ["pass_refute", "send_chat"]),
                "refute_cards": options,
                "pending_refute": deepcopy(pending_refute),
            }

        if seat_id != self.active_seat_id:
            return {"available": ["send_chat"]}

        seat = self.seat(seat_id)
        available = {"send_chat", "accuse", "end_turn"}
        phase = str(self.state["phase"])
        room_name = self.current_room(seat_id)
        move_targets: list[dict[str, Any]] = []

        if phase == "start_turn":
            available.add("roll")
            if room_name:
                available.add("suggest")
                node_id = ROOM_NAME_TO_NODE[room_name]
                if node_id in SECRET_PASSAGES:
                    passage_target = SECRET_PASSAGES[node_id]
                    move_targets.append(
                        {
                            "node_id": passage_target,
                            "label": BOARD_NODES[passage_target]["label"],
                            "cost": 0,
                            "mode": "passage",
                        }
                    )
                    available.add("move")
        if phase == "move":
            available.add("move")
            reachable = reachable_nodes(
                str(seat["position"]),
                int(self.state["remaining_steps"]),
                blocked=self.occupied_hallways(exclude_seat=seat_id),
            )
            for node_id, distance in sorted(reachable.items(), key=lambda item: (item[1], item[0])):
                if node_id in self.occupied_hallways(exclude_seat=seat_id):
                    continue
                move_targets.append(
                    {
                        "node_id": node_id,
                        "label": BOARD_NODES[node_id]["label"],
                        "cost": distance,
                        "mode": "walk",
                    }
                )
        if phase in {"start_turn", "post_move", "post_suggest"} and room_name and not self.state["turn_suggestion_used"]:
            available.add("suggest")
        return {
            "available": sorted(available),
            "move_targets": move_targets,
            "current_room": room_name,
            "roll_value": self.state["current_roll"],
            "remaining_steps": self.state["remaining_steps"],
        }

    def apply_action(self, seat_id: str, action: Action) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Validate and apply one seat action, returning the next state and emitted events.

        Every non-chat action eventually funnels through this dispatcher. It
        enforces turn ownership before delegating to the more specific phase
        handlers below.
        """

        kind = str(action.get("action", "")).strip().lower()
        if not kind:
            raise ValueError("Missing action.")
        if kind == "send_chat":
            return self.state, [self._apply_chat(seat_id, action)]
        pending_refute = self.state.get("pending_refute")
        if pending_refute:
            return self._apply_refute_action(seat_id, kind, action, pending_refute)
        if seat_id != self.active_seat_id:
            raise ValueError("Only the active seat may act right now.")
        if kind == "roll":
            return self._apply_roll()
        if kind == "move":
            return self._apply_move(seat_id, action)
        if kind == "suggest":
            return self._apply_suggest(seat_id, action)
        if kind == "accuse":
            return self._apply_accuse(seat_id, action)
        if kind == "end_turn":
            return self._advance_turn("Turn ended.")
        raise ValueError(f"Unsupported action: {kind}")

    def _apply_chat(self, seat_id: str, action: Action) -> dict[str, Any]:
        """Turn one chat payload into a public event row."""

        text = str(action.get("text", "")).strip()
        if not text:
            raise ValueError("Chat text must not be empty.")
        speaker = self.seat(seat_id)["display_name"]
        return make_event(
            "chat_posted",
            payload={"seat_id": seat_id, "text": text},
            message=f"{speaker}: {text}",
        )

    def _apply_roll(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Roll movement and transition the active seat into the move phase."""

        if self.state["phase"] != "start_turn":
            raise ValueError("You cannot roll right now.")
        roll_value = int(self._rng.randint(1, 6))
        self.state["phase"] = "move"
        self.state["current_roll"] = roll_value
        self.state["remaining_steps"] = roll_value
        return self.state, [
            make_event(
                "rolled",
                payload={"seat_id": self.active_seat_id, "roll": roll_value},
                message=f"{self.seat(self.active_seat_id)['display_name']} rolled {roll_value}.",
            )
        ]

    def _apply_move(self, seat_id: str, action: Action) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Apply a hallway walk or secret-passage move for the active seat."""

        target_node = str(action.get("target_node", "")).strip()
        if target_node not in BOARD_NODES:
            raise ValueError("Unknown movement target.")
        current_node = str(self.seat(seat_id)["position"])
        phase = str(self.state["phase"])
        events: list[dict[str, Any]] = []
        if phase == "start_turn":
            if current_node not in SECRET_PASSAGES or SECRET_PASSAGES[current_node] != target_node:
                raise ValueError("Only a secret passage move is legal before rolling.")
            self.seat(seat_id)["position"] = target_node
            self.state["phase"] = "post_move"
            self.state["current_roll"] = None
            self.state["remaining_steps"] = 0
            events.append(
                make_event(
                    "moved",
                    payload={"seat_id": seat_id, "from": current_node, "to": target_node, "mode": "passage"},
                    message=f"{self.seat(seat_id)['display_name']} used a secret passage to {BOARD_NODES[target_node]['label']}.",
                )
            )
            return self.state, events
        if phase != "move":
            raise ValueError("You cannot move right now.")
        reachable = reachable_nodes(
            current_node,
            int(self.state["remaining_steps"]),
            blocked=self.occupied_hallways(exclude_seat=seat_id),
        )
        if target_node not in reachable:
            raise ValueError("That destination is not reachable with the current roll.")
        self.seat(seat_id)["position"] = target_node
        self.state["remaining_steps"] = 0
        self.state["current_roll"] = None
        self.state["phase"] = "post_move" if is_room(target_node) else "start_turn"
        events.append(
            make_event(
                "moved",
                payload={"seat_id": seat_id, "from": current_node, "to": target_node, "mode": "walk"},
                message=f"{self.seat(seat_id)['display_name']} moved to {BOARD_NODES[target_node]['label']}.",
            )
        )
        if not is_room(target_node):
            _, turn_events = self._advance_turn("Turn ended after movement.")
            events.extend(turn_events)
        return self.state, events

    def _apply_suggest(self, seat_id: str, action: Action) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Apply an in-room suggestion and open the refutation sequence if needed."""

        room_name = self.current_room(seat_id)
        if not room_name:
            raise ValueError("Suggestions may only be made from inside a room.")
        if self.state["turn_suggestion_used"]:
            raise ValueError("This seat has already suggested this turn.")
        suspect = str(action.get("suspect", "")).strip()
        weapon = str(action.get("weapon", "")).strip()
        if suspect not in SUSPECTS or weapon not in WEAPONS:
            raise ValueError("Suggestions must name a valid suspect and weapon.")
        suggestion = {"suspect": suspect, "weapon": weapon, "room": room_name}
        events = [
            make_event(
                "suggestion_made",
                payload={"seat_id": seat_id, "suggestion": suggestion},
                message=f"{self.seat(seat_id)['display_name']} suggested {suspect} with the {weapon} in the {room_name}.",
            )
        ]
        self.state["turn_suggestion_used"] = True
        self.state["phase"] = "post_suggest"
        moved_seat_id = self._seat_id_for_character(suspect)
        if moved_seat_id is not None:
            self.seat(moved_seat_id)["position"] = ROOM_NAME_TO_NODE[room_name]
            if moved_seat_id != seat_id:
                events.append(
                    make_event(
                        "suspect_moved",
                        payload={"seat_id": moved_seat_id, "character": suspect, "room": room_name},
                        message=f"{suspect} was moved into the {room_name}.",
                    )
                )

        refute_order = self._refute_order(seat_id)
        current_refuter = None
        for candidate in refute_order:
            if self._refute_cards_for_seat(candidate, suggestion):
                current_refuter = candidate
                break
        if current_refuter is None:
            events.append(
                make_event(
                    "suggestion_unanswered",
                    payload={"seat_id": seat_id, "suggestion": suggestion},
                    message="No one could refute the suggestion.",
                )
            )
            self.state["pending_refute"] = None
        else:
            self.state["pending_refute"] = {
                "suggester": seat_id,
                "suggestion": suggestion,
                "order": refute_order,
                "current_refuter": current_refuter,
            }
            self.state["phase"] = "await_refute"
        return self.state, events

    def _apply_refute_action(
        self,
        seat_id: str,
        kind: str,
        action: Action,
        pending_refute: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Resolve one seat's turn in the private/public refutation chain."""

        if seat_id != str(pending_refute["current_refuter"]):
            raise ValueError("This seat is not currently being asked to refute.")
        suggestion = dict(pending_refute["suggestion"])
        refute_cards = self._refute_cards_for_seat(seat_id, suggestion)
        if kind == "show_refute_card":
            card_name = str(action.get("card", "")).strip()
            if card_name not in refute_cards:
                raise ValueError("That card cannot legally refute the current suggestion.")
            self.state["pending_refute"] = None
            self.state["phase"] = "post_suggest"
            public_event = make_event(
                "suggestion_refuted",
                payload={"seat_id": seat_id, "suggester": pending_refute["suggester"]},
                message=f"{self.seat(seat_id)['display_name']} refuted the suggestion.",
            )
            private_event = make_event(
                "private_card_shown",
                payload={"from_seat_id": seat_id, "to_seat_id": pending_refute["suggester"], "card": card_name},
                message=f"{self.seat(seat_id)['display_name']} privately showed {card_name}.",
                visibility=f"seat:{pending_refute['suggester']}",
            )
            return self.state, [public_event, private_event]
        if kind != "pass_refute":
            raise ValueError("The current refuter must show a card or pass.")
        if refute_cards:
            raise ValueError("This seat must show a matching card.")
        events = [
            make_event(
                "refute_passed",
                payload={"seat_id": seat_id},
                message=f"{self.seat(seat_id)['display_name']} could not refute.",
            )
        ]
        order = list(pending_refute["order"])
        current_index = order.index(seat_id)
        next_refuter = None
        for candidate in order[current_index + 1 :]:
            if self._refute_cards_for_seat(candidate, suggestion):
                next_refuter = candidate
                break
        if next_refuter is None:
            self.state["pending_refute"] = None
            self.state["phase"] = "post_suggest"
            events.append(
                make_event(
                    "suggestion_unanswered",
                    payload={"seat_id": pending_refute["suggester"], "suggestion": suggestion},
                    message="No one could refute the suggestion.",
                )
            )
        else:
            self.state["pending_refute"]["current_refuter"] = next_refuter
        return self.state, events

    def _apply_accuse(self, seat_id: str, action: Action) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Resolve an accusation, including elimination or game completion."""

        accusation = {
            "suspect": str(action.get("suspect", "")).strip(),
            "weapon": str(action.get("weapon", "")).strip(),
            "room": str(action.get("room", "")).strip(),
        }
        if accusation["suspect"] not in SUSPECTS or accusation["weapon"] not in WEAPONS or accusation["room"] not in ROOMS:
            raise ValueError("Accusations must provide a valid suspect, weapon, and room.")
        events = [
            make_event(
                "accusation_made",
                payload={"seat_id": seat_id, "accusation": accusation},
                message=(
                    f"{self.seat(seat_id)['display_name']} accused {accusation['suspect']} "
                    f"with the {accusation['weapon']} in the {accusation['room']}."
                ),
            )
        ]
        if accusation == self.case_file():
            self.state["status"] = "complete"
            self.state["winner_seat_id"] = seat_id
            self.state["phase"] = "game_over"
            events.append(
                make_event(
                    "accusation_correct",
                    payload={"seat_id": seat_id},
                    message=f"{self.seat(seat_id)['display_name']} solved the case.",
                )
            )
            return self.state, events
        self.seat(seat_id)["can_win"] = False
        events.append(
            make_event(
                "accusation_wrong",
                payload={"seat_id": seat_id},
                message=f"{self.seat(seat_id)['display_name']} was wrong and can no longer win.",
            )
        )
        remaining = [candidate for candidate in self.state["seat_order"] if self.seat(candidate)["can_win"]]
        if len(remaining) == 1:
            winner = remaining[0]
            self.state["status"] = "complete"
            self.state["winner_seat_id"] = winner
            self.state["phase"] = "game_over"
            events.append(
                make_event(
                    "game_completed",
                    payload={"winner_seat_id": winner},
                    message=f"{self.seat(winner)['display_name']} wins by elimination.",
                )
            )
            return self.state, events
        _, turn_events = self._advance_turn("Turn ended after accusation.")
        events.extend(turn_events)
        return self.state, events

    def _advance_turn(self, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Close the current turn and rotate to the next seat that can still win."""

        if self.state["status"] != "active":
            return self.state, []
        events = [make_event("turn_ended", payload={"seat_id": self.active_seat_id}, message=message)]
        next_seat = self._next_turn_seat(self.active_seat_id)
        self.state["turn_index"] = int(self.state["turn_index"]) + 1
        self.state["active_seat_id"] = next_seat
        self.state["phase"] = "start_turn"
        self.state["current_roll"] = None
        self.state["remaining_steps"] = 0
        self.state["turn_suggestion_used"] = False
        self.state["pending_refute"] = None
        events.append(
            make_event(
                "turn_started",
                payload={"seat_id": next_seat, "turn_index": self.state["turn_index"]},
                message=f"It is now {self.seat(next_seat)['display_name']}'s turn.",
            )
        )
        return self.state, events

    def _seat_id_for_character(self, character: str) -> str | None:
        """Look up the seat currently controlling one named character token."""

        for seat_id, seat in self.state["seats"].items():
            if seat["character"] == character:
                return seat_id
        return None

    def _next_turn_seat(self, from_seat: str) -> str:
        """Find the next seat in turn order that remains eligible to win."""

        order = list(self.state["seat_order"])
        start_index = order.index(from_seat)
        for offset in range(1, len(order) + 1):
            candidate = order[(start_index + offset) % len(order)]
            if self.seat(candidate)["can_win"]:
                return candidate
        return from_seat

    def _refute_order(self, suggester_id: str) -> list[str]:
        """Return the clockwise refutation order after one suggestion."""

        order = list(self.state["seat_order"])
        start = order.index(suggester_id)
        return [order[(start + offset) % len(order)] for offset in range(1, len(order))]

    def _refute_cards_for_seat(self, seat_id: str, suggestion: dict[str, str]) -> list[str]:
        """List the cards in a seat hand that can legally refute one suggestion."""

        hand = set(self.hidden_hand(seat_id))
        return sorted([card for card in suggestion.values() if card in hand])


def build_filtered_snapshot(
    state: dict[str, Any],
    *,
    seat_id: str,
    visible_events: list[dict[str, Any]],
    notebook: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the full game state into the public/private view for one seat.

    This is the privacy boundary shared by browser and autonomous-seat paths.
    Anything added here is potentially visible outside the raw persisted state,
    so public versus seat-private routing should stay explicit and easy to audit.
    """

    game = GameMaster(state)
    hidden = state["hidden"]
    analysis = dict(state.get("analysis") or {})
    social = dict(state.get("social") or {})
    public_seats = []
    for other_seat_id in state["seat_order"]:
        seat = state["seats"][other_seat_id]
        public_seats.append(
            {
                "seat_id": other_seat_id,
                "display_name": seat["display_name"],
                "character": seat["character"],
                "seat_kind": seat["seat_kind"],
                "agent_model": seat["agent_model"],
                "agent_profile": seat.get("agent_profile", ""),
                "agent_chat_model": seat.get("agent_chat_model", ""),
                "agent_chat_profile": seat.get("agent_chat_profile", ""),
                "ui_mode": _ui_mode_for_seat(state, other_seat_id),
                "position": seat["position"],
                "can_win": bool(seat["can_win"]),
                "hand_count": int(seat["hand_count"]),
            }
        )
    return {
        "game_id": state["game_id"],
        "title": state["title"],
        "ui_mode": _ui_mode_for_seat(state, seat_id),
        "status": state["status"],
        "turn_index": state["turn_index"],
        "active_seat_id": state["active_seat_id"],
        "phase": state["phase"],
        "winner_seat_id": state["winner_seat_id"],
        "current_roll": state["current_roll"],
        "remaining_steps": state["remaining_steps"],
        "board_nodes": list(BOARD_NODES.values()),
        "board_edges": [
            {
                "from": node_id,
                "to": neighbor,
                "kind": ("passage" if SECRET_PASSAGES.get(node_id) == neighbor else "walk"),
            }
            for node_id, neighbors in GRAPH.items()
            for neighbor in neighbors
            if node_id < neighbor
        ],
        "secret_passages": deepcopy(SECRET_PASSAGES),
        "seat": state["seats"][seat_id] | {"hand": list(hidden["hands"][seat_id])},
        "seats": public_seats,
        "legal_actions": game.legal_actions(seat_id),
        "events": visible_events,
        "notebook": notebook or {},
        "social": _filtered_social_snapshot(state, social, seat_id),
        "analysis": {
            "run_context": dict(analysis.get("run_context") or {}),
            "agent_runtime": dict(analysis.get("agent_runtime") or {}),
            "latency_targets_ms": dict(analysis.get("latency_targets_ms") or {}),
            "game_metrics": dict(analysis.get("game_metrics") or {}),
            "recent_turn_metrics": list(analysis.get("turn_metrics") or [])[-10:],
            "seat_debug": dict((analysis.get("latest_private_debug_by_seat") or {}).get(seat_id) or {}),
        },
        "case_file_categories": deepcopy(CARD_CATEGORIES),
    }


def _ui_mode_from_state(state: dict[str, Any]) -> str:
    """Return the persisted table-default UI mode."""

    ui_mode = normalize_ui_mode(state.get("ui_mode"))
    return ui_mode if ui_mode in LIVE_UI_MODES else DEFAULT_UI_MODE


def _ui_mode_for_seat(state: dict[str, Any], seat_id: str) -> str:
    """Return the UI mode for one seat, defaulting older games to Beginner."""

    seat = dict((state.get("seats") or {}).get(seat_id) or {})
    ui_mode = normalize_ui_mode(seat.get("ui_mode") or _ui_mode_from_state(state))
    return ui_mode if ui_mode in LIVE_UI_MODES else DEFAULT_UI_MODE


def _filtered_social_snapshot(state: dict[str, Any], social: dict[str, Any], seat_id: str) -> dict[str, Any]:
    """Return the seat-safe social memory slice used by chat and action runtimes."""

    seat_social = dict((social.get("seats") or {}).get(seat_id) or {})
    visible_threads = []
    for thread in list(social.get("threads") or []):
        thread_map = dict(thread or {})
        participants = [str(item) for item in list(thread_map.get("participants") or []) if str(item)]
        if seat_id not in participants:
            continue
        visible_threads.append(
            {
                "thread_id": str(thread_map.get("thread_id") or ""),
                "kind": str(thread_map.get("kind") or ""),
                "topic": str(thread_map.get("topic") or ""),
                "participants": participants,
                "heat": int(thread_map.get("heat") or 0),
                "status": str(thread_map.get("status") or ""),
                "burst_count": int(thread_map.get("burst_count") or 0),
                "last_event_index": int(thread_map.get("last_event_index") or 0),
            }
        )
    hottest_thread = max(
        visible_threads,
        key=lambda item: (int(item.get("heat") or 0), -int(item.get("last_event_index") or 0), str(item.get("thread_id") or "")),
        default={},
    )
    return {
        "seat_state": {
            "mood": str(seat_social.get("mood") or "calm"),
            "focus_seat_id": str(seat_social.get("focus_seat_id") or ""),
            "speaking_streak": int(seat_social.get("speaking_streak") or 0),
            "recent_intents": list(seat_social.get("recent_intents") or [])[-4:],
            "relationships": deepcopy(dict(seat_social.get("relationships") or {})),
            "last_chat_event_index": int(seat_social.get("last_chat_event_index") or 0),
        },
        "active_threads": visible_threads[-6:],
        "hottest_thread": hottest_thread,
    }
