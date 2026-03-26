from __future__ import annotations

from clue_agents.heuristic import HeuristicSeatAgent
from clue_core.deduction import ToolSnapshot


def test_home_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Multi-seat Clue" in html
    assert "Mrs. Peacock" in html
    assert "Professor Plum" in html
    assert "Set unused seats to NP." in html
    assert "Seed" not in html


def test_create_game_and_snapshot_flow(client):
    response = client.post(
        "/api/v1/games",
        json={
            "title": "Integration Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "human"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["game_id"].startswith("clue_")
    assert len(payload["seat_links"]) == 3

    token = payload["seat_links"][0]["url"].split("/join/", 1)[1]
    snapshot_response = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token})
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.get_json()
    assert snapshot["title"] == "Integration Table"
    assert snapshot["seat"]["character"] == "Miss Scarlet"
    assert len(snapshot["seat"]["hand"]) == snapshot["seat"]["hand_count"]
    assert snapshot["events"]


def test_notebook_update_persists_per_seat(client):
    response = client.post("/api/v1/games", json={})
    token = response.get_json()["seat_links"][0]["url"].split("/join/", 1)[1]
    notebook_response = client.post(
        "/api/v1/games/current/notebook",
        headers={"X-Clue-Seat-Token": token},
        json={"notebook": {"text": "Colonel Mustard is suspicious."}},
    )
    assert notebook_response.status_code == 200
    snapshot = notebook_response.get_json()
    assert snapshot["notebook"]["text"] == "Colonel Mustard is suspicious."


def test_game_page_renders_private_and_public_table_sections(client):
    response = client.post("/api/v1/games", json={})
    token = response.get_json()["seat_links"][0]["url"].split("/join/", 1)[1]
    page = client.get(f"/game?token={token}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Private Intel" in html
    assert "Move Grid" in html
    assert "Marker Grid" in html
    assert "Table Record" in html
    assert "Public Table Talk" in html


def test_create_game_supports_np_seats_and_all_six_characters(client):
    response = client.post(
        "/api/v1/games",
        json={
            "title": "Six Seat Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "heuristic"},
                {"seat_id": "seat_white", "display_name": "Mrs. White", "character": "Mrs. White", "seat_kind": "human"},
                {"seat_id": "seat_green", "display_name": "Mr. Green", "character": "Mr. Green", "seat_kind": "llm"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "np"},
                {"seat_id": "seat_plum", "display_name": "Professor Plum", "character": "Professor Plum", "seat_kind": "np"},
            ],
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert len(payload["seat_links"]) == 4
    assert {item["character"] for item in payload["seat_links"]} == {
        "Miss Scarlet",
        "Colonel Mustard",
        "Mrs. White",
        "Mr. Green",
    }


def test_create_game_requires_three_active_seats(client):
    response = client.post(
        "/api/v1/games",
        json={
            "title": "Too Small",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "np"},
                {"seat_id": "seat_white", "display_name": "Mrs. White", "character": "Mrs. White", "seat_kind": "human"},
            ],
        },
    )
    assert response.status_code == 400
    assert "between 3 and 6" in response.get_json()["error"]


def test_create_game_can_reuse_same_seat_ids_across_multiple_games(client):
    payload = {
        "title": "Repeated Seats",
        "seats": [
            {"seat_id": "seat_scarlet", "display_name": "Escarletta", "character": "Miss Scarlet", "seat_kind": "human"},
            {"seat_id": "seat_mustard", "display_name": "Mostardo", "character": "Colonel Mustard", "seat_kind": "human"},
            {"seat_id": "seat_peacock", "display_name": "Pavo", "character": "Mrs. Peacock", "seat_kind": "human"},
        ],
    }
    first = client.post("/api/v1/games", json=payload)
    second = client.post("/api/v1/games", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.get_json()["game_id"] != second.get_json()["game_id"]


def test_mixed_seat_agents_can_finish_full_game_with_mocked_llm(client, monkeypatch):
    heuristic = HeuristicSeatAgent()

    def _mock_llm_decide(self, *, snapshot, tool_snapshot):
        return heuristic.decide_turn(snapshot=snapshot, tool_snapshot=tool_snapshot)

    def _fast_tool_snapshot(self, state, seat_id, visible_events):
        return ToolSnapshot(
            envelope_marginals={},
            top_hypotheses=[],
            suggestion_ranking=[],
            accusation={
                "accusation": dict(state["hidden"]["case_file"]),
                "confidence": 1.0,
                "should_accuse": True,
            },
            sample_count=1,
        )

    monkeypatch.setattr("clue_agents.llm.LLMSeatAgent.decide_turn", _mock_llm_decide)
    monkeypatch.setattr("clue_web.runtime.GameService._tool_snapshot_for", _fast_tool_snapshot)

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Mixed Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "llm"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "heuristic"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "heuristic"},
                {"seat_id": "seat_plum", "display_name": "Professor Plum", "character": "Professor Plum", "seat_kind": "llm"},
            ],
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    token = payload["seat_links"][0]["url"].split("/join/", 1)[1]

    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()
    assert snapshot["status"] == "complete"
    assert snapshot["winner_seat_id"] == "seat_scarlet"
