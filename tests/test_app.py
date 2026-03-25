from __future__ import annotations


def test_home_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Multi-seat Clue" in html


def test_create_game_and_snapshot_flow(client):
    response = client.post(
        "/api/v1/games",
        json={
            "title": "Integration Table",
            "seed": 9,
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
