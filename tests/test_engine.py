"""Rules-engine tests for setup, suggestion/refutation flow, and visibility filtering."""

from __future__ import annotations

import random

from clue_core.engine import GameMaster, build_filtered_snapshot
from clue_core.setup import build_hidden_setup, build_initial_state
from clue_core.types import SeatConfig


def _seats() -> list[SeatConfig]:
    """Return a compact three-seat table used across engine tests."""

    return [
        SeatConfig(seat_id="seat_scarlet", display_name="Miss Scarlet", character="Miss Scarlet", seat_kind="human"),
        SeatConfig(seat_id="seat_mustard", display_name="Colonel Mustard", character="Colonel Mustard", seat_kind="human"),
        SeatConfig(seat_id="seat_peacock", display_name="Mrs. Peacock", character="Mrs. Peacock", seat_kind="human"),
    ]


def test_setup_deals_full_deck_without_duplicates():
    """The hidden deal should cover all cards exactly once across hands and case file."""

    seats = _seats()
    hidden_setup = build_hidden_setup(seats, seed=7)
    all_visible = []
    for hand in hidden_setup["hands"].values():
        all_visible.extend(hand)
    all_cards = set(all_visible + list(hidden_setup["case_file"].values()))
    assert len(all_visible) == 18
    assert len(all_cards) == 21
    assert hidden_setup["case_file"]["room"] not in all_visible


def test_setup_accepts_three_to_six_seats():
    """Setup should support every legal Clue player count."""

    characters = [
        "Miss Scarlet",
        "Colonel Mustard",
        "Mrs. White",
        "Mr. Green",
        "Mrs. Peacock",
        "Professor Plum",
    ]
    for count in range(3, 7):
        seats = [
            SeatConfig(seat_id=f"seat_{index}", display_name=character, character=character, seat_kind="human")
            for index, character in enumerate(characters[:count])
        ]
        hidden_setup = build_hidden_setup(seats, seed=17)
        assert sum(len(hand) for hand in hidden_setup["hands"].values()) == 18


def test_refutation_creates_public_and_private_events():
    """Showing a refute card should emit both table-public and suggester-private events."""

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
    """A wrong accusation should eliminate only the acting seat, not end the game outright."""

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


def test_suggestion_moves_named_suspect_into_room():
    """Suggestions should drag the named suspect token into the suggested room."""

    seats = _seats()
    hidden_setup = build_hidden_setup(seats, seed=5)
    state = build_initial_state("game_test", "Table", seats, hidden_setup)
    state["seats"]["seat_scarlet"]["position"] = "study"
    state["seats"]["seat_mustard"]["position"] = "hall_1"
    new_state, events = GameMaster(state).apply_action(
        "seat_scarlet",
        {"action": "suggest", "suspect": "Colonel Mustard", "weapon": "Knife"},
    )
    assert new_state["seats"]["seat_mustard"]["position"] == "study"
    assert any(event["event_type"] == "suspect_moved" for event in events)


def test_private_card_event_is_visible_only_to_suggester():
    """Private shown-card events must not leak into other seat snapshots."""

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

    state_after_suggest, _ = GameMaster(state).apply_action(
        "seat_scarlet",
        {"action": "suggest", "suspect": "Colonel Mustard", "weapon": "Knife"},
    )
    state_after_refute, events = GameMaster(state_after_suggest).apply_action(
        "seat_mustard",
        {"action": "show_refute_card", "card": "Colonel Mustard"},
    )

    scarlet_snapshot = build_filtered_snapshot(state_after_refute, seat_id="seat_scarlet", visible_events=events, notebook={})
    peacock_snapshot = build_filtered_snapshot(state_after_refute, seat_id="seat_peacock", visible_events=[events[0]], notebook={})

    assert any(event["event_type"] == "private_card_shown" for event in scarlet_snapshot["events"])
    assert not any(event["event_type"] == "private_card_shown" for event in peacock_snapshot["events"])


def test_filtered_snapshot_defaults_missing_ui_mode_to_beginner():
    """Older persisted games without a UI mode should render as Beginner Mode."""

    seats = _seats()
    hidden_setup = build_hidden_setup(seats, seed=9)
    state = build_initial_state("game_test", "Table", seats, hidden_setup)

    snapshot = build_filtered_snapshot(state, seat_id="seat_scarlet", visible_events=[], notebook={})

    assert snapshot["ui_mode"] == "beginner"


def test_filtered_snapshot_uses_seat_ui_mode_before_table_default():
    """Seat-specific UI mode should control the viewer's filtered snapshot."""

    seats = _seats()
    hidden_setup = build_hidden_setup(seats, seed=10)
    state = build_initial_state("game_test", "Table", seats, hidden_setup)
    state["ui_mode"] = "beginner"
    state["seats"]["seat_scarlet"]["ui_mode"] = "player"

    scarlet_snapshot = build_filtered_snapshot(state, seat_id="seat_scarlet", visible_events=[], notebook={})
    mustard_snapshot = build_filtered_snapshot(state, seat_id="seat_mustard", visible_events=[], notebook={})

    assert scarlet_snapshot["ui_mode"] == "player"
    assert mustard_snapshot["ui_mode"] == "beginner"


def test_move_targets_do_not_include_start_nodes():
    """Legal movement targets should never send a seat back onto a start node."""

    seats = _seats()
    hidden_setup = build_hidden_setup(seats, seed=11)
    state = build_initial_state("game_test", "Table", seats, hidden_setup)
    state["seats"]["seat_scarlet"]["position"] = "hall_lounge"
    state["phase"] = "move"
    state["current_roll"] = 1
    state["remaining_steps"] = 1

    legal = GameMaster(state).legal_actions("seat_scarlet")
    targets = {item["node_id"] for item in legal["move_targets"]}

    assert "scarlet_start" not in targets
    assert {"hall", "lounge"} <= targets
