"""Focused tests for the deterministic heuristic seat policy."""

from __future__ import annotations

from clue_agents.heuristic import HeuristicSeatAgent


def _snapshot(*, legal_actions: dict | None = None, turn_index: int = 0, events: list[dict] | None = None) -> dict:
    """Build a minimal seat snapshot for unit-testing the heuristic policy."""

    return {
        "turn_index": turn_index,
        "phase": "post_suggest",
        "seat": {
            "seat_id": "seat_scarlet",
            "display_name": "Miss Scarlet",
            "character": "Miss Scarlet",
            "hand": ["Candlestick", "Hall"],
        },
        "legal_actions": legal_actions or {"available": ["end_turn"]},
        "events": events or [],
    }


def test_heuristic_delays_early_accusation_until_case_is_better_supported():
    """High confidence alone should not trigger an immediate accusation on an early turn."""

    agent = HeuristicSeatAgent()
    decision = agent.decide_turn(
        snapshot=_snapshot(legal_actions={"available": ["accuse", "end_turn"]}, turn_index=1),
        tool_snapshot={
            "accusation": {
                "should_accuse": True,
                "confidence": 0.88,
                "confidence_gap": 0.21,
                "entropy_bits": 0.74,
                "sample_count": 64,
                "accusation": {
                    "suspect": "Colonel Mustard",
                    "weapon": "Knife",
                    "room": "Hall",
                },
            },
            "belief_summary": {
                "joint_case_entropy_bits": 0.74,
                "resolved_cards": 5,
                "case_file_candidate_counts": {"suspect": 2, "weapon": 2, "room": 2},
            },
        },
    )

    assert decision.action == "end_turn"
    assert "too early" in " ".join(decision.debug_private["accusation_window"]["hold_reasons"])


def test_heuristic_suggests_with_character_specific_public_flavor():
    """Suggestion actions should carry a short stock in-character public line."""

    agent = HeuristicSeatAgent()
    decision = agent.decide_turn(
        snapshot=_snapshot(
            legal_actions={"available": ["suggest"], "current_room": "Library"},
            turn_index=5,
        ),
        tool_snapshot={
            "suggestion_ranking": [
                {
                    "suspect": "Professor Plum",
                    "weapon": "Rope",
                    "room": "Library",
                    "why": "Best information gain in the current room.",
                }
            ],
            "belief_summary": {"joint_case_entropy_bits": 1.42},
            "accusation": {"should_accuse": False},
        },
    )

    assert decision.action == "suggest"
    assert decision.suspect == "Professor Plum"
    assert decision.weapon == "Rope"
    assert "Library" in str(decision.text)


def test_heuristic_moves_toward_the_more_informative_room():
    """Movement should prefer the path that points at the highest-value room lead."""

    agent = HeuristicSeatAgent()
    decision = agent.decide_turn(
        snapshot={
            **_snapshot(
                legal_actions={
                    "available": ["move"],
                    "move_targets": [
                        {"node_id": "study_hall", "label": "Study / Hall", "cost": 1, "mode": "walk"},
                        {"node_id": "hall_billiard", "label": "Hall / Billiard", "cost": 1, "mode": "walk"},
                    ],
                },
                turn_index=4,
            ),
            "phase": "move",
        },
        tool_snapshot={
            "envelope_marginals": {"room": {"Study": 0.08, "Hall": 0.12, "Billiard Room": 0.58}},
            "top_hypotheses": [{"suspect": "Mrs. Peacock", "weapon": "Rope", "room": "Billiard Room", "p": 0.42}],
            "belief_summary": {"joint_case_entropy_bits": 1.26},
            "accusation": {"should_accuse": False},
        },
    )

    assert decision.action == "move"
    assert decision.target_node == "hall_billiard"
