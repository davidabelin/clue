"""Integration-style tests for the standalone Clue page and API surface."""

from __future__ import annotations

from pathlib import Path

from clue_agents.base import ChatDecision
from clue_agents.heuristic import HeuristicSeatAgent
from clue_core.deduction import ToolSnapshot
from clue_web import create_app


def _token_from_join_url(url: str) -> str:
    """Extract the signed seat token from one relative join URL."""

    return str(url).split("join/", 1)[1]


def _chat_events(snapshot: dict) -> list[dict]:
    """Return the player-facing public chat events from one snapshot."""

    return [
        event
        for event in snapshot.get("events", [])
        if event.get("visibility") == "public"
        and event.get("event_type") == "chat_posted"
        and not str(event.get("event_type", "")).startswith("trace_")
    ]


def test_home_page_renders(client):
    """The landing page should expose the intended create-game affordances."""

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Multi-seat Clue" in html
    assert "Mrs. Peacock" in html
    assert "Professor Plum" in html
    assert "Set unused seats to NP." in html
    assert "Clue v1.7.0 runs as a standalone lab" in html
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
    """The game page should render the redesigned caseboard, desk, and briefing sections."""

    response = client.post("/api/v1/games", json={})
    token = _token_from_join_url(response.get_json()["seat_links"][0]["url"])
    page = client.get(f"/game?token={token}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Caseboard" in html
    assert "Decision Desk" in html
    assert "Private Briefing" in html
    assert "Private Intel" in html
    assert "Marker Grid" in html
    assert "Case Notes" in html
    assert "Table Wire" in html
    assert "Witness Record" in html
    assert "Table Talk" in html
    assert "Chat Feed" in html
    assert "Seat Debug" in html
    assert "How LLM Seats Work" in html
    assert "Suspect Lineup" in html
    assert "Advanced Diagnostics" in html


def test_idle_chat_limits_one_npc_message_per_sweep(client, monkeypatch):
    """One snapshot refresh should append at most one NPC chat reply."""

    monkeypatch.setattr("clue_web.runtime.GameService._idle_chat_roll", lambda self, game_id, seat_id, event_index: 0.0)
    monkeypatch.setattr(
        "clue_agents.llm.LLMSeatAgent.decide_chat",
        lambda self, *, snapshot: ChatDecision(speak=True, text=f"{snapshot['seat']['display_name']} checks in.", rationale_private="chat"),
    )

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Idle Chat Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "llm"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "llm"},
            ],
        },
    )
    token = _token_from_join_url(response.get_json()["seat_links"][0]["url"])

    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()

    assert len(_chat_events(snapshot)) == 1


def test_idle_chat_does_not_repeat_for_same_public_event(client, monkeypatch):
    """Repeated snapshot polls should not duplicate the same NPC reaction."""

    monkeypatch.setattr("clue_web.runtime.GameService._idle_chat_roll", lambda self, game_id, seat_id, event_index: 0.0)
    monkeypatch.setattr(
        "clue_agents.llm.LLMSeatAgent.decide_chat",
        lambda self, *, snapshot: ChatDecision(speak=True, text="Steady now.", rationale_private="chat"),
    )

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Stable Poll Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "llm"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    token = _token_from_join_url(response.get_json()["seat_links"][0]["url"])

    first = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()
    second = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()

    assert len(_chat_events(first)) == 1
    assert len(_chat_events(second)) == 1


def test_human_chat_can_trigger_idle_npc_reply_without_advancing_turn(client, monkeypatch):
    """Human off-turn chat should be able to provoke one NPC reply without changing turn control."""

    monkeypatch.setattr("clue_web.runtime.GameService._idle_chat_roll", lambda self, game_id, seat_id, event_index: 0.0)

    def _reply(self, *, snapshot):
        seat = snapshot["seat"]
        return ChatDecision(speak=True, text=f"{seat['display_name']} heard that.", rationale_private="chat")

    monkeypatch.setattr("clue_agents.llm.LLMSeatAgent.decide_chat", _reply)

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Reply Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "llm"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "llm"},
            ],
        },
    )
    token = _token_from_join_url(response.get_json()["seat_links"][0]["url"])

    snapshot = client.post(
        "/api/v1/games/current/actions",
        headers={"X-Clue-Seat-Token": token},
        json={"action": "send_chat", "text": "Mrs. Peacock, that sounded rehearsed."},
    ).get_json()

    chats = _chat_events(snapshot)
    assert snapshot["active_seat_id"] == "seat_scarlet"
    assert len(chats) >= 2
    assert chats[-1]["payload"]["seat_id"] == "seat_peacock"


def test_idle_chat_skips_while_nonhuman_turn_is_pending(client, monkeypatch):
    """Snapshot polling should not run idle chatter while a non-human rules action is queued."""

    monkeypatch.setattr("clue_web.runtime.GameService.maybe_run_agents", lambda self, game_id, max_cycles=32: None)
    monkeypatch.setattr("clue_web.runtime.GameService._idle_chat_roll", lambda self, game_id, seat_id, event_index: 0.0)
    monkeypatch.setattr(
        "clue_agents.llm.LLMSeatAgent.decide_chat",
        lambda self, *, snapshot: ChatDecision(speak=True, text="This should not post.", rationale_private="chat"),
    )

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Pending Agent Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "llm"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "human"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    token = _token_from_join_url(response.get_json()["seat_links"][1]["url"])

    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()

    assert snapshot["active_seat_id"] == "seat_scarlet"
    assert _chat_events(snapshot) == []


def test_idle_chat_cooldown_requires_two_later_public_events(client, monkeypatch):
    """An NPC should stay quiet for two later public events before chatting again."""

    monkeypatch.setattr("clue_web.runtime.GameService._idle_chat_roll", lambda self, game_id, seat_id, event_index: 0.0)
    monkeypatch.setattr(
        "clue_agents.llm.LLMSeatAgent.decide_chat",
        lambda self, *, snapshot: ChatDecision(speak=True, text="Noted.", rationale_private="chat"),
    )

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Cooldown Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "llm"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    token = _token_from_join_url(response.get_json()["seat_links"][0]["url"])

    first = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()
    second = client.post(
        "/api/v1/games/current/actions",
        headers={"X-Clue-Seat-Token": token},
        json={"action": "send_chat", "text": "First follow-up."},
    ).get_json()
    third = client.post(
        "/api/v1/games/current/actions",
        headers={"X-Clue-Seat-Token": token},
        json={"action": "send_chat", "text": "Second follow-up."},
    ).get_json()
    fourth = client.post(
        "/api/v1/games/current/actions",
        headers={"X-Clue-Seat-Token": token},
        json={"action": "send_chat", "text": "Third follow-up."},
    ).get_json()

    assert len(_chat_events(first)) == 1
    assert len(_chat_events(second)) == 2
    assert len(_chat_events(third)) == 3
    assert len(_chat_events(fourth)) == 5


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
    """LLM seats without explicit model settings should receive turn and chat profiles."""

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
    assert llm_link["agent_chat_profile"]

    token = _token_from_join_url(llm_link["url"])
    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()

    assert snapshot["seat"]["agent_profile"] == llm_link["agent_profile"]
    assert snapshot["seat"]["agent_chat_profile"] == llm_link["agent_chat_profile"]
    assert snapshot["seat"]["agent_model"]
    assert snapshot["seat"]["agent_chat_model"]
    assert snapshot["social"]["seat_state"]["relationships"]


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
    assert snapshot["analysis"]["run_context"]["release_label"] == "v1.7.0"
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
