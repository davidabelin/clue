"""Integration-style tests for the standalone Clue page and API surface."""

from __future__ import annotations

from pathlib import Path

from clue_agents.heuristic import HeuristicSeatAgent
from clue_core.deduction import ToolSnapshot
from clue_web import create_app


def _token_from_join_url(url: str) -> str:
    """Extract the signed seat token from one relative join URL."""

    return str(url).split("join/", 1)[1]


def test_home_page_renders(client):
    """The landing page should expose the intended create-game affordances."""

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Multi-seat Clue" in html
    assert "Mrs. Peacock" in html
    assert "Professor Plum" in html
    assert "Set unused seats to NP." in html
    assert "Clue v1.5.0 runs as a standalone lab" in html
    assert "Seed" not in html
    assert ">Heuristic<" not in html
    assert 'fetch("api/v1/games"' in html
    assert 'fetch("/api/v1/games"' not in html


def test_create_game_and_snapshot_flow(client):
    """Creating a game should return invite links and a valid seat snapshot flow."""

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
    assert payload["seat_links"][0]["url"].startswith("join/")

    token = _token_from_join_url(payload["seat_links"][0]["url"])
    snapshot_response = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token})
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.get_json()
    assert snapshot["title"] == "Integration Table"
    assert snapshot["seat"]["character"] == "Miss Scarlet"
    assert len(snapshot["seat"]["hand"]) == snapshot["seat"]["hand_count"]
    assert snapshot["events"]


def test_notebook_update_persists_per_seat(client):
    """Notebook saves should persist inside one seat's private snapshot."""

    response = client.post("/api/v1/games", json={})
    token = _token_from_join_url(response.get_json()["seat_links"][0]["url"])
    notebook_response = client.post(
        "/api/v1/games/current/notebook",
        headers={"X-Clue-Seat-Token": token},
        json={"notebook": {"text": "Colonel Mustard is suspicious."}},
    )
    assert notebook_response.status_code == 200
    snapshot = notebook_response.get_json()
    assert snapshot["notebook"]["text"] == "Colonel Mustard is suspicious."


def test_game_page_renders_private_and_public_table_sections(client):
    """The restored game page should render the older board, seat, and record sections."""

    response = client.post("/api/v1/games", json={})
    token = _token_from_join_url(response.get_json()["seat_links"][0]["url"])
    page = client.get(f"/game?token={token}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Board" in html
    assert "Private Seat" in html
    assert "Private Intel" in html
    assert "Marker Grid" in html
    assert "Table Record" in html
    assert "Public Table Talk" in html
    assert "Seat Debug" in html
    assert "How LLM Seats Work" in html
    assert "Table Seats" in html
    assert "Round Table" not in html
    assert "Players" not in html


def test_create_game_supports_np_seats_and_all_six_characters(client):
    """NP seats should be excluded while still supporting all six canonical characters."""

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
    assert {item["seat_kind"] for item in payload["seat_links"]} <= {"human", "llm"}


def test_create_game_normalizes_legacy_heuristic_seats_to_llm(client):
    """Legacy heuristic payloads should still work while storing only human/llm seats."""

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Normalized Seats",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "heuristic"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "human"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    seat_link = next(item for item in payload["seat_links"] if item["seat_id"] == "seat_scarlet")

    assert seat_link["seat_kind"] == "llm"

    token = _token_from_join_url(seat_link["url"])
    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()

    assert snapshot["seat"]["seat_kind"] == "llm"


def test_create_game_assigns_yaml_model_profile_to_llm_seats(client):
    """LLM seats without explicit model settings should receive a selected profile."""

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Profiled LLM Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "llm"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "human"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    llm_link = next(item for item in payload["seat_links"] if item["seat_id"] == "seat_scarlet")

    assert llm_link["agent_profile"]

    token = _token_from_join_url(llm_link["url"])
    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()

    assert snapshot["seat"]["agent_profile"] == llm_link["agent_profile"]
    assert snapshot["seat"]["agent_model"]


def test_create_game_requires_three_active_seats(client):
    """Game creation should reject tables with fewer than three active seats."""

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
    """Seat ids should be scoped per game rather than globally unique forever."""

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


def test_autonomous_turns_persist_analysis_metrics_and_private_debug(client):
    """Autonomous turns should persist eval metrics plus seat-private debug payloads."""

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Telemetry Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "heuristic"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "human"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    token = _token_from_join_url(payload["seat_links"][0]["url"])
    other_token = _token_from_join_url(payload["seat_links"][1]["url"])

    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()
    other_snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": other_token}).get_json()
    assert snapshot["analysis"]["run_context"]["release_label"] == "v1.5.0"
    assert snapshot["analysis"]["agent_runtime"]["sdk_backend"] == "openai_agents_sdk"
    assert snapshot["analysis"]["agent_runtime"]["default_model"] == "gpt-5.4-mini-2026-03-17"
    assert snapshot["analysis"]["game_metrics"]["autonomous_actions"] >= 1
    assert snapshot["analysis"]["recent_turn_metrics"]
    assert snapshot["analysis"]["seat_debug"]["decision"]["action"]
    assert snapshot["analysis"]["seat_debug"]["tool_snapshot"]["top_hypotheses"]
    assert other_snapshot["analysis"]["seat_debug"] == {}


def test_mixed_seat_agents_can_finish_full_game_with_mocked_llm(client, monkeypatch):
    """A mixed heuristic/LLM table should finish when the LLM path is mocked deterministically."""

    heuristic = HeuristicSeatAgent()

    def _mock_llm_decide(self, *, snapshot, tool_snapshot):
        """Reuse the heuristic policy as a deterministic stand-in for the LLM policy."""

        return heuristic.decide_turn(snapshot=snapshot, tool_snapshot=tool_snapshot)

    def _fast_tool_snapshot(self, state, seat_id, visible_events):
        """Force a trivial perfect-knowledge tool snapshot so the game finishes quickly."""

        return ToolSnapshot(
            envelope_marginals={},
            top_hypotheses=[],
            suggestion_ranking=[],
            accusation={
                "accusation": dict(state["hidden"]["case_file"]),
                "confidence": 1.0,
                "confidence_gap": 1.0,
                "entropy_bits": 0.0,
                "should_accuse": True,
                "sample_count": 48,
            },
            belief_summary={
                "joint_case_entropy_bits": 0.0,
                "resolved_cards": 12,
                "case_file_candidate_counts": {"suspect": 1, "weapon": 1, "room": 1},
            },
            sample_count=48,
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
    token = _token_from_join_url(payload["seat_links"][0]["url"])

    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()
    assert snapshot["status"] == "complete"
    assert snapshot["winner_seat_id"] == "seat_scarlet"


def test_create_app_loads_database_url_from_secret(monkeypatch, tmp_path: Path):
    """App config should populate DATABASE_URL from Secret Manager when requested."""

    def _fake_read_secret(version_name: str) -> str:
        """Return a temporary SQLite URL in place of a real secret lookup."""

        assert version_name == "projects/p/secrets/clue-db-url/versions/latest"
        return f"sqlite+pysqlite:///{(tmp_path / 'secret.db').as_posix()}"

    monkeypatch.setattr("clue_web._read_secret_version", _fake_read_secret)
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": "",
            "DATABASE_URL_SECRET": "projects/p/secrets/clue-db-url/versions/latest",
        }
    )
    assert str(app.config["DATABASE_URL"]).startswith("sqlite+pysqlite:///")
