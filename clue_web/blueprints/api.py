"""JSON endpoints for gameplay, chat, notebook saves, and agent worker execution."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _token_from_request() -> str:
    token = str(request.headers.get("X-Clue-Seat-Token", "")).strip()
    if token:
        return token
    return str(request.args.get("token", "")).strip()


@api_bp.post("/games")
def create_game():
    payload = request.get_json(silent=True) or {}
    result = current_app.extensions["game_service"].create_game(payload)
    return jsonify(result), 201


@api_bp.get("/games/current")
def current_snapshot():
    token = _token_from_request()
    since_event_index = int(request.args.get("since", "0") or 0)
    snapshot = current_app.extensions["game_service"].snapshot_for_token(token, since_event_index=since_event_index)
    return jsonify(snapshot)


@api_bp.post("/games/current/actions")
def submit_action():
    token = _token_from_request()
    payload = request.get_json(silent=True) or {}
    snapshot = current_app.extensions["game_service"].submit_action(token, payload)
    return jsonify(snapshot)


@api_bp.post("/games/current/notebook")
def update_notebook():
    token = _token_from_request()
    payload = request.get_json(silent=True) or {}
    notebook = dict(payload.get("notebook") or {})
    snapshot = current_app.extensions["game_service"].update_notebook(token, notebook)
    return jsonify(snapshot)


@api_bp.post("/internal/games/<game_id>/run-agents")
def run_agents(game_id: str):
    expected = str(current_app.config.get("INTERNAL_WORKER_TOKEN", "")).strip()
    provided = str(request.headers.get("X-Clue-Worker-Token", "")).strip()
    if expected and provided != expected:
        return jsonify({"status": "forbidden"}), 403
    current_app.extensions["game_service"].maybe_run_agents(game_id)
    return jsonify({"status": "ok", "game_id": game_id})
