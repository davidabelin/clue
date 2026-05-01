"""Integration-style tests for the standalone Clue page and API surface."""

from __future__ import annotations

from pathlib import Path

from clue_agents.base import ChatDecision, MemorySummaryDecision
from clue_agents.heuristic import HeuristicSeatAgent
from clue_core.deduction import ToolSnapshot
from clue_core.events import make_event
from clue_core.version import CLUE_RELEASE_LABEL
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
    assert 'name="ui_mode_0"' in html
    assert '<option value="beginner" selected>Beginner</option>' in html
    assert '<option value="player">Player</option>' in html
    assert '<option value="superplayer" disabled>Superplayer (later)</option>' in html
    assert f"Clue {CLUE_RELEASE_LABEL} runs as a standalone lab" in html
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
    assert snapshot["ui_mode"] == "beginner"
    assert snapshot["seat"]["character"] == "Miss Scarlet"
    assert len(snapshot["seat"]["hand"]) == snapshot["seat"]["hand_count"]
    assert snapshot["events"]


def test_create_game_uses_fresh_setup_seed(client, app, monkeypatch):
    """Game creation should not deal every table from one fixed setup seed."""

    seeds = iter([101, 202])
    monkeypatch.setattr("clue_web.runtime._new_game_seed", lambda: next(seeds))
    seats = [
        {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
        {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "human"},
        {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
    ]

    first = client.post("/api/v1/games", json={"title": "Seed One", "seats": seats}).get_json()
    second = client.post("/api/v1/games", json={"title": "Seed Two", "seats": seats}).get_json()

    repository = app.extensions["game_service"]._repository
    first_record = repository.get_game_record(first["game_id"])
    second_record = repository.get_game_record(second["game_id"])

    assert first_record["config"]["seed"] == 101
    assert first_record["setup"]["seed"] == 101
    assert first_record["state"]["hidden"]["seed"] == 101
    assert second_record["config"]["seed"] == 202
    assert second_record["setup"]["seed"] == 202
    assert second_record["state"]["hidden"]["seed"] == 202


def test_create_game_accepts_player_mode(client):
    """Player Mode should persist per seat through game creation into snapshots."""

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Player Table",
            "seats": [
                {
                    "seat_id": "seat_scarlet",
                    "display_name": "Miss Scarlet",
                    "character": "Miss Scarlet",
                    "seat_kind": "human",
                    "ui_mode": "player",
                },
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "human"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    assert response.status_code == 201

    payload = response.get_json()
    scarlet_token = _token_from_join_url(payload["seat_links"][0]["url"])
    mustard_token = _token_from_join_url(payload["seat_links"][1]["url"])
    scarlet_snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": scarlet_token}).get_json()
    mustard_snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": mustard_token}).get_json()

    assert payload["seat_links"][0]["ui_mode"] == "player"
    assert scarlet_snapshot["ui_mode"] == "player"
    assert scarlet_snapshot["seat"]["ui_mode"] == "player"
    assert mustard_snapshot["ui_mode"] == "beginner"


def test_create_game_top_level_ui_mode_remains_a_default_for_legacy_clients(client):
    """Legacy top-level mode payloads should still seed seats that omit ui_mode."""

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Legacy Player Table",
            "ui_mode": "player",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "human"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    assert response.status_code == 201

    token = _token_from_join_url(response.get_json()["seat_links"][0]["url"])
    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()

    assert snapshot["ui_mode"] == "player"


def test_create_game_rejects_invalid_or_unavailable_ui_modes(client):
    """Only live UI modes should be accepted by the create-game API."""

    base_payload = {
        "title": "Mode Guard Table",
        "seats": [
            {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
            {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "human"},
            {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
        ],
    }

    table_superplayer = client.post("/api/v1/games", json=base_payload | {"ui_mode": "superplayer"})
    seat_superplayer = client.post(
        "/api/v1/games",
        json=base_payload
        | {
            "seats": [
                base_payload["seats"][0] | {"ui_mode": "superplayer"},
                base_payload["seats"][1],
                base_payload["seats"][2],
            ]
        },
    )
    invalid = client.post("/api/v1/games", json=base_payload | {"ui_mode": "expert"})

    assert table_superplayer.status_code == 400
    assert "Superplayer mode is not available yet" in table_superplayer.get_json()["error"]
    assert seat_superplayer.status_code == 400
    assert "Superplayer mode is not available yet" in seat_superplayer.get_json()["error"]
    assert invalid.status_code == 400
    assert "ui_mode must be one of" in invalid.get_json()["error"]


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
    assert "Turn Readout" in html
    assert "Action Queue" in html
    assert "Private Briefing" in html
    assert "Seat Snapshot" in html
    assert "Reveals" in html
    assert "Marker Grid" in html
    assert "Notes" in html
    assert "Table Wire" in html
    assert "Witness Record" in html
    assert "Table Talk" in html
    assert "Chat Feed" in html
    assert "Quit Game" in html
    assert 'href="/"' in html
    assert "Seat Debug" in html
    assert "How LLM Seats Work" in html
    assert "Suspect Lineup" in html
    assert "Advanced Diagnostics" in html


def test_player_mode_board_movement_static_contract():
    """Player Mode should keep board movement primary while move targets exist."""

    js = Path("clue_web/static/js/clue.js").read_text(encoding="utf-8")
    css = Path("clue_web/static/css/clue.css").read_text(encoding="utf-8")

    assert "Move On Board" in js
    assert "clickable-edge" in js
    assert "data-board-target" in js
    assert "edge-hit-area" in js
    assert 'available.has("end_turn") && !playerBoardMove' in js
    assert 'available.has("accuse") && !playerBoardMove' in js
    assert "width: min(100%, 52rem)" in css
    assert ".game-app--player .board-svg {\n  width: 100%;" in css
    assert '"caseboard desk"' in css


def test_snapshot_does_not_run_optional_chat_by_default(client, app, monkeypatch):
    """Stabilization defaults should keep optional chat off during normal snapshots."""

    monkeypatch.setattr("clue_web.runtime.GameService._idle_chat_roll", lambda self, game_id, seat_id, event_index: 0.0)
    calls = {"count": 0}

    def _reply(self, *, snapshot):
        calls["count"] += 1
        raise AssertionError("optional chat should be disabled by default")

    monkeypatch.setattr("clue_agents.llm.LLMSeatAgent.decide_chat", _reply)

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Quiet Default Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "llm"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    token = _token_from_join_url(response.get_json()["seat_links"][0]["url"])

    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()

    assert _chat_events(snapshot) == []
    assert calls["count"] == 0
    assert app.extensions["game_service"].admin_runtime_settings()["effective"]["idle_chat_enabled"] is False


def test_idle_chat_limits_one_npc_message_per_sweep(client, app, monkeypatch):
    """One snapshot refresh should append at most one NPC chat reply."""

    app.extensions["game_service"].update_admin_runtime_settings({"idle_chat_enabled": True})
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


def test_idle_chat_does_not_repeat_for_same_public_event(client, app, monkeypatch):
    """Repeated snapshot polls should not duplicate the same NPC reaction."""

    app.extensions["game_service"].update_admin_runtime_settings({"idle_chat_enabled": True})
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


def test_proactive_idle_chat_runs_once_after_model_silence(client, app, monkeypatch):
    """After reactive silence, quiet-table proactive chat should be throttled per turn."""

    app.extensions["game_service"].update_admin_runtime_settings({"idle_chat_enabled": True, "proactive_chat_enabled": True})
    monkeypatch.setattr("clue_web.runtime.GameService._idle_chat_roll", lambda self, game_id, seat_id, event_index: 0.0)
    calls = {"count": 0}

    def _reply(self, *, snapshot):
        calls["count"] += 1
        if calls["count"] == 1:
            return ChatDecision(speak=False, rationale_private="silence")
        return ChatDecision(speak=True, text="The room has gone interestingly quiet.", rationale_private="proactive")

    monkeypatch.setattr("clue_agents.llm.LLMSeatAgent.decide_chat", _reply)

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Proactive Table",
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
    third = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()

    assert _chat_events(first) == []
    assert len(_chat_events(second)) == 1
    assert len(_chat_events(third)) == 1


def test_admin_runtime_setting_can_disable_idle_chat(client, app, monkeypatch):
    """The session-only idle-chat setting should stop snapshot-triggered NHP chat."""

    app.extensions["game_service"].update_admin_runtime_settings({"idle_chat_enabled": True})
    app.extensions["game_service"].update_admin_runtime_settings({"idle_chat_enabled": False})
    monkeypatch.setattr("clue_web.runtime.GameService._idle_chat_roll", lambda self, game_id, seat_id, event_index: 0.0)
    calls = {"count": 0}

    def _reply(self, *, snapshot):
        calls["count"] += 1
        return ChatDecision(speak=True, text="This should stay quiet.", rationale_private="chat")

    monkeypatch.setattr("clue_agents.llm.LLMSeatAgent.decide_chat", _reply)

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Idle Disabled Table",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "llm"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    token = _token_from_join_url(response.get_json()["seat_links"][0]["url"])

    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": token}).get_json()

    assert _chat_events(snapshot) == []
    assert calls["count"] == 0


def test_admin_runtime_setting_can_disable_only_proactive_chat(client, app, monkeypatch):
    """Disabling proactive chat should preserve reactive replies."""

    app.extensions["game_service"].update_admin_runtime_settings({"idle_chat_enabled": True, "proactive_chat_enabled": False})
    monkeypatch.setattr("clue_web.runtime.GameService._idle_chat_roll", lambda self, game_id, seat_id, event_index: 0.0)
    calls = {"count": 0}

    def _reply(self, *, snapshot):
        calls["count"] += 1
        if calls["count"] == 1:
            return ChatDecision(speak=False, rationale_private="reactive silence")
        return ChatDecision(speak=True, text="Reactive still works.", rationale_private="reactive")

    monkeypatch.setattr("clue_agents.llm.LLMSeatAgent.decide_chat", _reply)

    response = client.post(
        "/api/v1/games",
        json={
            "title": "Proactive Disabled Table",
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
    third = client.post(
        "/api/v1/games/current/actions",
        headers={"X-Clue-Seat-Token": token},
        json={"action": "send_chat", "text": "Colonel Mustard, respond."},
    ).get_json()

    assert _chat_events(first) == []
    assert _chat_events(second) == []
    assert calls["count"] == 2
    assert _chat_events(third)[-1]["payload"]["text"] == "Reactive still works."


def test_human_chat_can_trigger_idle_npc_reply_without_advancing_turn(client, app, monkeypatch):
    """Human off-turn chat should be able to provoke one NPC reply without changing turn control."""

    app.extensions["game_service"].update_admin_runtime_settings({"idle_chat_enabled": True})
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


def test_idle_chat_skips_while_nonhuman_turn_is_pending(client, app, monkeypatch):
    """Snapshot polling should not run idle chatter while a non-human rules action is queued."""

    app.extensions["game_service"].update_admin_runtime_settings({"idle_chat_enabled": True})
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


def test_idle_chat_cooldown_requires_two_later_public_events(client, app, monkeypatch):
    """An NPC should stay quiet for two later public events before chatting again."""

    app.extensions["game_service"].update_admin_runtime_settings({"idle_chat_enabled": True})
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


def test_autonomous_turns_persist_analysis_metrics_and_private_debug(client, monkeypatch):
    """Autonomous turns should persist eval metrics plus seat-private debug payloads."""

    heuristic = HeuristicSeatAgent()

    def _mock_llm_decide(self, *, snapshot, tool_snapshot):
        """Use the deterministic policy as an explicit test double for live LLM output."""

        return heuristic.decide_turn(snapshot=snapshot, tool_snapshot=tool_snapshot)

    monkeypatch.setattr("clue_agents.llm.LLMSeatAgent.decide_turn", _mock_llm_decide)

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
    assert snapshot["analysis"]["run_context"]["release_label"] == CLUE_RELEASE_LABEL
    assert snapshot["analysis"]["agent_runtime"]["sdk_backend"] == "openai_agents_sdk"
    assert snapshot["analysis"]["agent_runtime"]["default_model"] == "gpt-5.4-mini-2026-03-17"
    assert snapshot["analysis"]["game_metrics"]["autonomous_actions"] >= 1
    assert snapshot["analysis"]["recent_turn_metrics"]
    assert snapshot["analysis"]["seat_debug"]["decision"]["action"]
    assert snapshot["analysis"]["seat_debug"]["tool_snapshot"]["top_hypotheses"]
    assert other_snapshot["analysis"]["seat_debug"] == {}


def test_unavailable_llm_turn_does_not_use_heuristic_fallback(client):
    """An LLM seat without credentials should stop and report the failure, not fake a turn."""

    response = client.post(
        "/api/v1/games",
        json={
            "title": "No Fake LLM",
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "llm"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "human"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    llm_token = _token_from_join_url(payload["seat_links"][0]["url"])
    human_token = _token_from_join_url(payload["seat_links"][1]["url"])
    other_human_token = _token_from_join_url(payload["seat_links"][2]["url"])
    snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": human_token}).get_json()
    llm_snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": llm_token}).get_json()
    other_snapshot = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": other_human_token}).get_json()

    assert snapshot["active_seat_id"] == "seat_scarlet"
    public_failure = next(event for event in snapshot["events"] if event["event_type"] == "llm_unavailable")
    assert public_failure["visibility"] == "public"
    assert public_failure["payload"] == {
        "seat_id": "seat_scarlet",
        "seat_kind": "llm",
        "reason": "missing_api_key",
        "mode": "turn",
    }
    assert "no heuristic move was used" in public_failure["message"]
    assert "debug" not in public_failure["payload"]
    assert "runtime" not in public_failure["payload"]
    metric = snapshot["analysis"]["recent_turn_metrics"][-1]
    assert metric["action"] == "llm_unavailable"
    assert metric["fallback_used"] is False
    assert metric["llm_error_reason"] == "missing_api_key"
    assert snapshot["analysis"]["seat_debug"] == {}
    assert other_snapshot["analysis"]["seat_debug"] == {}
    assert llm_snapshot["analysis"]["seat_debug"]["decision"]["action"] == "llm_unavailable"
    assert llm_snapshot["analysis"]["seat_debug"]["decision"]["agent_meta"]["llm_error_reason"] == "missing_api_key"
    assert llm_snapshot["analysis"]["seat_debug"]["decision_debug"]["llm_runtime"]


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


def _create_table_without_agents(client, monkeypatch, *, title: str = "Memory Table") -> dict:
    """Create one mixed table while suppressing automatic autonomous turns."""

    monkeypatch.setattr("clue_web.runtime.GameService.maybe_run_agents", lambda self, game_id, max_cycles=32: None)
    response = client.post(
        "/api/v1/games",
        json={
            "title": title,
            "seats": [
                {"seat_id": "seat_scarlet", "display_name": "Miss Scarlet", "character": "Miss Scarlet", "seat_kind": "human"},
                {"seat_id": "seat_mustard", "display_name": "Colonel Mustard", "character": "Colonel Mustard", "seat_kind": "llm"},
                {"seat_id": "seat_peacock", "display_name": "Mrs. Peacock", "character": "Mrs. Peacock", "seat_kind": "human"},
            ],
        },
    )
    assert response.status_code == 201
    return response.get_json()


def _mark_game_complete(app, game_id: str, *, winner_seat_id: str = "seat_mustard") -> dict:
    """Persist one game as complete for durable-memory runtime tests."""

    repository = app.extensions["repository"]
    state = repository.get_state(game_id)
    state["status"] = "complete"
    state["phase"] = "game_over"
    state["winner_seat_id"] = winner_seat_id
    repository.save_state_and_events(game_id, state=state, events=[])
    return state


def test_completed_game_writes_ready_nhp_memory_and_relationships(client, app, monkeypatch):
    """Completed games should store successful LLM-authored NHP memory summaries."""

    def _summary(self, *, snapshot):
        return MemorySummaryDecision(
            summary={
                "first_person_summary": "I won by keeping Scarlet off balance.",
                "strategic_lessons": ["Pressure early when the room is noisy."],
                "social_observations": ["Miss Scarlet noticed the pressure."],
                "relationship_updates": [
                    {
                        "target_kind": "hp",
                        "target_identity": "Miss Scarlet",
                        "target_display_name": "Miss Scarlet",
                        "affinity_delta": 1,
                        "trust_delta": -1,
                        "friction_delta": 1,
                        "note": "Scarlet pushed back after I won.",
                    }
                ],
            },
            agent_meta={"model": "memory-test", "session_id": "game:seat:memory"},
        )

    monkeypatch.setattr("clue_agents.llm.LLMSeatAgent.summarize_memory", _summary)
    payload = _create_table_without_agents(client, monkeypatch)
    state = _mark_game_complete(app, payload["game_id"])

    app.extensions["game_service"]._finalize_game_if_complete(payload["game_id"], state)

    memory = app.extensions["repository"].list_nhp_memory(agent_identity="Colonel Mustard")
    relationships = app.extensions["repository"].list_nhp_relationships(agent_identity="Colonel Mustard")
    assert memory[0]["status"] == "ready"
    assert memory[0]["summary"]["first_person_summary"].startswith("I won")
    assert relationships[0]["target_kind"] == "hp"
    assert relationships[0]["target_identity"] == "miss scarlet"


def test_missing_api_key_leaves_completed_game_memory_pending(client, app, monkeypatch):
    """Missing LLM credentials should queue memory without blocking completion."""

    payload = _create_table_without_agents(client, monkeypatch, title="Pending Memory Table")
    state = _mark_game_complete(app, payload["game_id"])

    app.extensions["game_service"]._finalize_game_if_complete(payload["game_id"], state)

    memory = app.extensions["repository"].list_nhp_memory(agent_identity="Colonel Mustard")
    assert memory[0]["status"] == "pending"
    assert memory[0]["failure_reason"] == "missing_api_key"
    assert memory[0]["retry_count"] == 1


def test_internal_nhp_snapshot_loads_memory_but_player_snapshot_does_not(client, app, monkeypatch):
    """Durable memory should feed NHP runtimes without leaking to browser snapshots."""

    payload = _create_table_without_agents(client, monkeypatch, title="Loaded Memory Table")
    game_id = payload["game_id"]
    repository = app.extensions["repository"]
    job = repository.ensure_nhp_memory_job(
        game_id=game_id,
        seat_id="seat_mustard",
        character="Colonel Mustard",
        display_name="Colonel Mustard",
    )
    repository.mark_nhp_memory_ready(
        job["id"],
        summary={"first_person_summary": "I remember Scarlet bluffing."},
        model_meta={"model": "test"},
    )

    internal = app.extensions["game_service"]._build_internal_snapshot(game_id, "seat_mustard")
    human_token = _token_from_join_url(payload["seat_links"][0]["url"])
    player = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": human_token}).get_json()

    assert internal["memory_context"]["ready_memories"][0]["summary"]["first_person_summary"]
    assert "memory_context" not in player


def test_write_sink_persists_notes_relationships_and_memory_context(client, app, monkeypatch):
    """Model-facing write tools should persist immediately without leaking to players."""

    payload = _create_table_without_agents(client, monkeypatch, title="Write Sink Table")
    game_id = payload["game_id"]

    result = app.extensions["game_service"]._write_sink.update_relationship(
        game_id=game_id,
        seat_id="seat_mustard",
        target_seat_id="seat_scarlet",
        affinity_delta=9,
        trust_delta=-9,
        friction_delta=1,
        note="Scarlet pushed too directly.",
        tool_name="update_relationship_posture",
        payload={"mode": "chat_intent"},
    )
    note_result = app.extensions["game_service"]._write_sink.record_note(
        game_id=game_id,
        seat_id="seat_mustard",
        target_seat_id="seat_scarlet",
        note_kind="memory_note",
        note_text="Scarlet responds to direct pressure.",
        tool_name="record_memory_note",
        payload={"mode": "turn"},
    )
    internal = app.extensions["game_service"]._build_internal_snapshot(game_id, "seat_mustard")
    human_token = _token_from_join_url(payload["seat_links"][0]["url"])
    player = client.get("/api/v1/games/current", headers={"X-Clue-Seat-Token": human_token}).get_json()

    relationships = app.extensions["repository"].list_nhp_relationships(agent_identity="Colonel Mustard")
    notes = app.extensions["repository"].list_nhp_notes(agent_identity="Colonel Mustard")

    assert result["status"] == "ok"
    assert note_result["status"] == "ok"
    assert relationships[0]["affinity"] == 2
    assert relationships[0]["trust"] == -2
    assert {note["note_kind"] for note in notes} >= {"relationship_update", "memory_note"}
    assert internal["memory_context"]["recent_notes"]
    assert "memory_context" not in player


def test_admin_endpoints_require_token_and_expose_memory(client, app, monkeypatch):
    """Administrator APIs should reject missing tokens and accept the configured token."""

    app.config["CLUE_ADMIN_TOKEN"] = "admin-test"
    payload = _create_table_without_agents(client, monkeypatch, title="Admin Memory Table")
    _mark_game_complete(app, payload["game_id"])
    app.extensions["repository"].record_nhp_note(
        agent_identity="Colonel Mustard",
        game_id=payload["game_id"],
        seat_id="seat_mustard",
        note_kind="memory_note",
        note_text="Admin-visible note.",
        tool_name="record_memory_note",
    )

    blocked = client.get("/api/v1/admin/games")
    allowed = client.get("/api/v1/admin/games", headers={"X-Clue-Admin-Token": "admin-test"})
    detail = client.get(f"/api/v1/admin/games/{payload['game_id']}", headers={"X-Clue-Admin-Token": "admin-test"})
    notes = client.get("/api/v1/admin/nhp-notes", headers={"X-Clue-Admin-Token": "admin-test"})
    nhp_history = client.get("/api/v1/admin/nhp-history", headers={"X-Clue-Admin-Token": "admin-test"})
    human_history = client.get("/api/v1/admin/human-history?player_identity=Miss%20Scarlet", headers={"X-Clue-Admin-Token": "admin-test"})
    home = client.get("/")
    admin_entry = client.get("/admin")
    invalid_admin_entry = client.get("/admin?admin_token=wrong")
    page = client.get("/admin?admin_token=admin-test")
    game_page = client.get(f"/admin/games/{payload['game_id']}?admin_token=admin-test")

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert allowed.get_json()["games"]
    assert detail.status_code == 200
    assert detail.get_json()["id"] == payload["game_id"]
    assert detail.get_json()["nhp_notes"]
    assert notes.get_json()["nhp_notes"]
    assert nhp_history.get_json()["nhp_history"]
    assert human_history.get_json()["human_history"][0]["player_identity"] == "miss scarlet"
    assert home.status_code == 200
    assert "Superplayer Admin" in home.get_data(as_text=True)
    assert admin_entry.status_code == 200
    admin_entry_html = admin_entry.get_data(as_text=True)
    assert "Open Superplayer Admin" in admin_entry_html
    assert "CLUE_ADMIN_TOKEN" in admin_entry_html
    assert "CLUE_ADMIN_TOKEN_SECRET" in admin_entry_html
    assert "clue-admin-token" in admin_entry_html
    assert "admin-test" not in admin_entry_html
    assert invalid_admin_entry.status_code == 403
    assert "Administrator token required." in invalid_admin_entry.get_data(as_text=True)
    assert page.status_code == 200
    page_html = page.get_data(as_text=True)
    assert "Superplayer Administration" in page_html
    assert "Game Review" in page_html
    assert "Durable Notes And Tool Writes" in page_html
    assert game_page.status_code == 200
    game_html = game_page.get_data(as_text=True)
    case_file = app.extensions["repository"].get_state(payload["game_id"])["hidden"]["case_file"]
    assert "Case File And Hands" in game_html
    assert case_file["suspect"] in game_html
    assert "Event Stream" in game_html
    assert "Raw State" in game_html


def test_admin_summaries_count_optional_chat_failures_separately(client, app, monkeypatch):
    """Private chat traces should not be mixed into gameplay-turn LLM failures."""

    payload = _create_table_without_agents(client, monkeypatch, title="Chat Failure Admin Table")
    game_id = payload["game_id"]
    repository = app.extensions["repository"]
    state = repository.get_state(game_id)
    metrics = state.setdefault("analysis", {}).setdefault("game_metrics", {})
    metrics["llm_unavailable_count"] = 2
    repository.save_state_and_events(
        game_id,
        state=state,
        events=[
            make_event(
                "trace_llm_unavailable",
                message="Colonel Mustard's LLM chat was unavailable.",
                visibility="seat:seat_mustard",
                payload={
                    "seat_id": "seat_mustard",
                    "seat_kind": "llm",
                    "mode": "chat",
                    "reason": "model_error",
                    "runtime": {"default_model": "gpt-chat-test"},
                    "debug": {"tool_writes": []},
                    "error": "Pydantic EOF while parsing AgentChatOutput",
                },
            )
        ],
    )

    dashboard = app.extensions["game_service"].admin_dashboard()
    summary = next(game for game in dashboard["games"] if game["id"] == game_id)
    review = app.extensions["game_service"].admin_game_review(game_id)

    assert summary["llm_unavailable_count"] == 2
    assert summary["chat_failure_count"] == 1
    assert dashboard["overview"]["llm_failures"] == 2
    assert dashboard["overview"]["chat_failures"] == 1
    assert review["chat_failures"]["count"] == 1
    assert review["chat_failures"]["breakdown"][0]["display_name"] == "Colonel Mustard"
    assert review["chat_failures"]["breakdown"][0]["reason"] == "model_error"
    assert "Pydantic EOF" in review["chat_failures"]["breakdown"][0]["sample_error_prefix"]


def test_admin_can_terminate_and_delete_saved_games(client, app, monkeypatch):
    """Admin dashboard controls should close stale games and remove dead clutter."""

    app.config["CLUE_ADMIN_TOKEN"] = "admin-test"
    payload = _create_table_without_agents(client, monkeypatch, title="Dead Game Table")
    game_id = payload["game_id"]

    page = client.get("/admin?admin_token=admin-test")
    page_html = page.get_data(as_text=True)
    assert "Terminate" in page_html
    assert "Delete" in page_html

    terminated = client.post(
        f"/admin/games/{game_id}/terminate",
        data={"admin_token": "admin-test"},
        follow_redirects=True,
    )
    state = app.extensions["repository"].get_state(game_id)

    assert terminated.status_code == 200
    assert "Game terminated." in terminated.get_data(as_text=True)
    assert state["status"] == "terminated"
    assert state["phase"] == "admin_terminated"
    assert any(event["event_type"] == "admin_game_terminated" for event in app.extensions["repository"].events_for_game(game_id))

    deleted = client.post(
        f"/admin/games/{game_id}/delete",
        data={"admin_token": "admin-test"},
        follow_redirects=True,
    )

    assert deleted.status_code == 200
    assert "Game deleted." in deleted.get_data(as_text=True)
    assert app.extensions["repository"].get_game_record(game_id) is None
    assert client.get(f"/admin/games/{game_id}?admin_token=admin-test").status_code == 404


def test_admin_runtime_settings_api_requires_token_and_clamps_values(client, app):
    """Administrator runtime settings should be protected and process-local."""

    app.config["CLUE_ADMIN_TOKEN"] = "admin-test"

    blocked = client.get("/api/v1/admin/runtime-settings")
    allowed = client.get("/api/v1/admin/runtime-settings", headers={"X-Clue-Admin-Token": "admin-test"})
    updated = client.post(
        "/api/v1/admin/runtime-settings",
        headers={"X-Clue-Admin-Token": "admin-test"},
        json={
            "idle_chat_enabled": True,
            "proactive_chat_enabled": True,
            "proactive_chat_chance_multiplier": 9,
        },
    )
    reset = client.post(
        "/api/v1/admin/runtime-settings",
        headers={"X-Clue-Admin-Token": "admin-test"},
        json={"reset": True},
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert allowed.get_json()["effective"]["idle_chat_enabled"] is False
    assert allowed.get_json()["effective"]["proactive_chat_enabled"] is False
    assert updated.status_code == 200
    assert updated.get_json()["effective"]["idle_chat_enabled"] is True
    assert updated.get_json()["effective"]["proactive_chat_enabled"] is True
    assert updated.get_json()["effective"]["proactive_chat_chance_multiplier"] == 1.0
    assert reset.get_json()["effective"]["idle_chat_enabled"] is False
    assert reset.get_json()["effective"]["proactive_chat_enabled"] is False
    assert app.extensions["runtime_overrides"] == {}


def test_admin_memory_retry_uses_pending_jobs(client, app, monkeypatch):
    """Admin retry should run pending memory jobs through the LLM summary path."""

    app.config["CLUE_ADMIN_TOKEN"] = "admin-test"
    payload = _create_table_without_agents(client, monkeypatch, title="Retry Memory Table")
    state = _mark_game_complete(app, payload["game_id"])
    app.extensions["game_service"]._finalize_game_if_complete(payload["game_id"], state)

    def _summary(self, *, snapshot):
        return MemorySummaryDecision(summary={"first_person_summary": "I remember the retry."}, agent_meta={"model": "retry-test"})

    monkeypatch.setattr("clue_agents.llm.LLMSeatAgent.summarize_memory", _summary)
    retry = client.post("/api/v1/admin/nhp-memory/retry", headers={"X-Clue-Admin-Token": "admin-test"}, json={})
    page_retry = client.post("/admin/nhp-memory/retry", data={"admin_token": "admin-test"}, follow_redirects=True)

    assert retry.status_code == 200
    assert retry.get_json()["attempted"] == 1
    assert app.extensions["repository"].list_nhp_memory(agent_identity="Colonel Mustard")[0]["status"] == "ready"
    assert page_retry.status_code == 200
    assert "Memory retry attempted" in page_retry.get_data(as_text=True)


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


def test_create_app_loads_admin_and_flask_secrets(monkeypatch, tmp_path: Path):
    """Secret Manager indirection should populate admin and Flask signing secrets."""

    def _fake_read_secret(version_name: str) -> str:
        values = {
            "projects/p/secrets/clue-secret-key/versions/latest": "signed-seat-secret",
            "projects/p/secrets/clue-admin-token/versions/latest": "admin-secret",
        }
        return values[version_name]

    monkeypatch.setattr("clue_web._read_secret_version", _fake_read_secret)
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "",
            "SECRET_KEY_SECRET": "projects/p/secrets/clue-secret-key/versions/latest",
            "CLUE_ADMIN_TOKEN": "",
            "CLUE_ADMIN_TOKEN_SECRET": "projects/p/secrets/clue-admin-token/versions/latest",
            "DB_PATH": str(tmp_path / "secret-config.db"),
        }
    )

    assert app.config["SECRET_KEY"] == "signed-seat-secret"
    assert app.config["CLUE_ADMIN_TOKEN"] == "admin-secret"
