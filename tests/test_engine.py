from __future__ import annotations

import random

from clue_core.engine import GameMaster
from clue_core.setup import build_hidden_setup, build_initial_state
from clue_core.types import SeatConfig


def _seats() -> list[SeatConfig]:
    return [
        SeatConfig(seat_id="seat_scarlet", display_name="Miss Scarlet", character="Miss Scarlet", seat_kind="human"),
        SeatConfig(seat_id="seat_mustard", display_name="Colonel Mustard", character="Colonel Mustard", seat_kind="human"),
        SeatConfig(seat_id="seat_peacock", display_name="Mrs. Peacock", character="Mrs. Peacock", seat_kind="human"),
    ]


def test_setup_deals_full_deck_without_duplicates():
    seats = _seats()
    hidden_setup = build_hidden_setup(seats, seed=7)
    all_visible = []
    for hand in hidden_setup["hands"].values():
        all_visible.extend(hand)
    all_cards = set(all_visible + list(hidden_setup["case_file"].values()))
    assert len(all_visible) == 18
    assert len(all_cards) == 21
    assert hidden_setup["case_file"]["room"] not in all_visible


def test_refutation_creates_public_and_private_events():
    seats = _seats()
    hidden_setup = {
        "seed": 1,
        "case_file": {"suspect": "Professor Plum", "weapon": "Rope", "room": "Kitchen"},
        "hands": {
            "seat_scarlet": ["Miss Scarlet", "Candlestick", "Study", "Hall", "Lounge", "Library"],
            "seat_mustard": ["Colonel Mustard", "Knife", "Billiard Room", "Dining Room", "Ballroom", "Mrs. White"],
            "seat_peacock": ["Mrs. Peacock", "Mr. Green", "Lead Pipe", "Revolver", "Wrench", "Conservatory"],
        },
    }
    state = build_initial_state("game_test", "Table", seats, hidden_setup)
    state["seats"]["seat_scarlet"]["position"] = "study"
    game = GameMaster(state, rng=random.Random(2))

    state_after_suggest, events = game.apply_action(
        "seat_scarlet",
        {"action": "suggest", "suspect": "Colonel Mustard", "weapon": "Knife"},
    )
    assert any(event["event_type"] == "suggestion_made" for event in events)
    assert state_after_suggest["pending_refute"]["current_refuter"] == "seat_mustard"

    state_after_refute, refute_events = GameMaster(state_after_suggest).apply_action(
        "seat_mustard",
        {"action": "show_refute_card", "card": "Colonel Mustard"},
    )
    assert state_after_refute["pending_refute"] is None
    assert refute_events[0]["visibility"] == "public"
    assert refute_events[1]["visibility"] == "seat:seat_scarlet"
    assert refute_events[1]["payload"]["card"] == "Colonel Mustard"


def test_wrong_accusation_eliminates_but_keeps_game_running():
    seats = _seats()
    hidden_setup = build_hidden_setup(seats, seed=3)
    state = build_initial_state("game_test", "Table", seats, hidden_setup)
    game = GameMaster(state)
    new_state, events = game.apply_action(
        "seat_scarlet",
        {"action": "accuse", "suspect": "Miss Scarlet", "weapon": "Knife", "room": "Hall"},
    )
    assert any(event["event_type"] == "accusation_wrong" for event in events)
    assert new_state["seats"]["seat_scarlet"]["can_win"] is False
    assert new_state["status"] == "active"
    assert new_state["active_seat_id"] != "seat_scarlet"
