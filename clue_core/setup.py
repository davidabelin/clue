"""Initial game setup and dealing helpers."""

from __future__ import annotations

import random
from typing import Any

from clue_core.board import CHARACTER_START_NODES
from clue_core.constants import ROOMS, SUSPECTS, WEAPONS
from clue_core.types import SeatConfig


def build_hidden_setup(seats: list[SeatConfig], *, seed: int | None = None) -> dict[str, Any]:
    """Choose the case file and deal the remaining cards across active seats."""

    if not (3 <= len(seats) <= 6):
        raise ValueError("Clue requires between 3 and 6 seats.")
    rng = random.Random(seed)
    case_file = {
        "suspect": rng.choice(list(SUSPECTS)),
        "weapon": rng.choice(list(WEAPONS)),
        "room": rng.choice(list(ROOMS)),
    }
    remaining_cards = [
        *[card for card in SUSPECTS if card != case_file["suspect"]],
        *[card for card in WEAPONS if card != case_file["weapon"]],
        *[card for card in ROOMS if card != case_file["room"]],
    ]
    rng.shuffle(remaining_cards)
    hands = {seat.seat_id: [] for seat in seats}
    for index, card_name in enumerate(remaining_cards):
        seat = seats[index % len(seats)]
        hands[seat.seat_id].append(card_name)
    return {
        "seed": seed,
        "case_file": case_file,
        "hands": hands,
    }


def build_initial_state(game_id: str, title: str, seats: list[SeatConfig], hidden_setup: dict[str, Any]) -> dict[str, Any]:
    """Build the first public+private game state snapshot from a hidden deal."""

    ordered_seat_ids = [seat.seat_id for seat in seats]
    seat_state = {}
    for seat in seats:
        seat_state[seat.seat_id] = {
            "seat_id": seat.seat_id,
            "display_name": seat.display_name,
            "character": seat.character,
            "seat_kind": seat.seat_kind,
            "agent_model": seat.agent_model,
            "position": CHARACTER_START_NODES[seat.character],
            "hand_count": len(hidden_setup["hands"][seat.seat_id]),
            "can_win": True,
        }
    return {
        "game_id": game_id,
        "title": title,
        "status": "active",
        "winner_seat_id": None,
        "turn_index": 0,
        "active_seat_id": ordered_seat_ids[0],
        "phase": "start_turn",
        "current_roll": None,
        "remaining_steps": 0,
        "turn_suggestion_used": False,
        "pending_refute": None,
        "seat_order": ordered_seat_ids,
        "seats": seat_state,
        "hidden": hidden_setup,
    }
