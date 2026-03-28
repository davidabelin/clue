"""Deduction-tool tests for entropy, opponent hooks, and telemetry-facing payloads."""

from __future__ import annotations

from clue_core.deduction import build_tool_snapshot


def test_build_tool_snapshot_exposes_entropy_and_opponent_model():
    """Tool snapshots should now include belief entropy, opponent hooks, and generation stats."""

    visible_events = [
        {
            "event_type": "suggestion_made",
            "payload": {
                "seat_id": "seat_scarlet",
                "suggestion": {
                    "suspect": "Colonel Mustard",
                    "weapon": "Knife",
                    "room": "Study",
                },
            },
        },
        {
            "event_type": "suggestion_refuted",
            "payload": {
                "seat_id": "seat_mustard",
                "suggester": "seat_scarlet",
            },
        },
        {
            "event_type": "accusation_made",
            "payload": {
                "seat_id": "seat_peacock",
                "accusation": {
                    "suspect": "Professor Plum",
                    "weapon": "Rope",
                    "room": "Kitchen",
                },
            },
        },
    ]

    snapshot = build_tool_snapshot(
        seat_id="seat_scarlet",
        seat_hand=["Miss Scarlet"],
        hand_counts={
            "seat_scarlet": 6,
            "seat_mustard": 6,
            "seat_peacock": 6,
        },
        visible_events=visible_events,
        room_name="Study",
        sample_count=32,
        time_budget_ms=50,
    )

    assert "joint_case_entropy_bits" in snapshot.belief_summary
    assert "top_suggestion_why" in snapshot.belief_summary
    assert snapshot.opponent_model["seats"]["seat_mustard"]["public_refutations_made"] >= 1
    assert snapshot.opponent_model["seats"]["seat_peacock"]["public_accusations"]
    assert snapshot.generation["elapsed_ms"] >= 0
    assert "expected_information_gain" in snapshot.suggestion_ranking[0]
    assert "opponent_leak_penalty" in snapshot.suggestion_ranking[0]

