"""JSON endpoints for gameplay, chat, notebook saves, and agent worker execution."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _token_from_request() -> str:
    """Read the seat token from the Clue header first, then the query string."""

    token = str(request.headers.get("X-Clue-Seat-Token", "")).strip()
    if token:
        return token
    return str(request.args.get("token", "")).strip()


def _error(message: str, status_code: int):
    """Return one consistent JSON error payload."""

    return jsonify({"error": message}), status_code


def _admin_authorized() -> bool:
    """Check the configured Administrator Mode token against header or query input."""

    expected = str(current_app.config.get("CLUE_ADMIN_TOKEN", "")).strip()
    provided = str(request.headers.get("X-Clue-Admin-Token", "") or request.args.get("admin_token", "")).strip()
    return bool(expected and provided == expected)


def _admin_forbidden():
    """Return the standard Administrator Mode authorization failure."""

    return _error("Administrator token required.", 403)


@api_bp.post("/games")
def create_game():
    """Create a new game and return per-seat invitation links."""

    payload = request.get_json(silent=True) or {}
    try:
        result = current_app.extensions["game_service"].create_game(payload)
    except ValueError as exc:
        return _error(str(exc), 400)
    return jsonify(result), 201


@api_bp.get("/games/current")
def current_snapshot():
    """Return the seat-filtered current snapshot for the provided token."""

    token = _token_from_request()
    since_event_index = int(request.args.get("since", "0") or 0)
    try:
        snapshot = current_app.extensions["game_service"].snapshot_for_token(token, since_event_index=since_event_index)
    except KeyError as exc:
        return _error(str(exc), 404)
    return jsonify(snapshot)


@api_bp.post("/games/current/actions")
def submit_action():
    """Apply one seat action and return the updated filtered snapshot."""

    token = _token_from_request()
    payload = request.get_json(silent=True) or {}
    try:
        snapshot = current_app.extensions["game_service"].submit_action(token, payload)
    except KeyError as exc:
        return _error(str(exc), 404)
    except ValueError as exc:
        return _error(str(exc), 400)
    return jsonify(snapshot)


@api_bp.post("/games/current/notebook")
def update_notebook():
    """Persist one seat's private notebook text and return the updated snapshot."""

    token = _token_from_request()
    payload = request.get_json(silent=True) or {}
    notebook = dict(payload.get("notebook") or {})
    try:
        snapshot = current_app.extensions["game_service"].update_notebook(token, notebook)
    except KeyError as exc:
        return _error(str(exc), 404)
    return jsonify(snapshot)


@api_bp.post("/internal/games/<game_id>/run-agents")
def run_agents(game_id: str):
    """Internal worker endpoint for running queued autonomous seat turns."""

    expected = str(current_app.config.get("INTERNAL_WORKER_TOKEN", "")).strip()
    provided = str(request.headers.get("X-Clue-Worker-Token", "")).strip()
    if expected and provided != expected:
        return jsonify({"status": "forbidden"}), 403
    current_app.extensions["game_service"].maybe_run_agents(game_id)
    return jsonify({"status": "ok", "game_id": game_id})


@api_bp.get("/admin/games")
def admin_games():
    """Return recent saved games for Administrator Mode."""

    if not _admin_authorized():
        return _admin_forbidden()
    return jsonify({"games": current_app.extensions["game_service"].admin_dashboard()["games"]})


@api_bp.get("/admin/games/<game_id>")
def admin_game_detail(game_id: str):
    """Return a full saved-game detail payload for Administrator Mode."""

    if not _admin_authorized():
        return _admin_forbidden()
    try:
        return jsonify(current_app.extensions["game_service"].admin_game_detail(game_id))
    except KeyError as exc:
        return _error(str(exc), 404)


@api_bp.get("/admin/nhp-memory")
def admin_nhp_memory():
    """Return durable NHP memory rows for Administrator Mode."""

    if not _admin_authorized():
        return _admin_forbidden()
    status = str(request.args.get("status", "") or "")
    agent_identity = str(request.args.get("agent_identity", "") or "")
    rows = current_app.extensions["repository"].list_nhp_memory(status=status, agent_identity=agent_identity, limit=250)
    return jsonify({"nhp_memory": rows})


@api_bp.get("/admin/relationships")
def admin_relationships():
    """Return durable NHP relationship rows for Administrator Mode."""

    if not _admin_authorized():
        return _admin_forbidden()
    agent_identity = str(request.args.get("agent_identity", "") or "")
    rows = current_app.extensions["repository"].list_nhp_relationships(agent_identity=agent_identity, limit=500)
    return jsonify({"relationships": rows})


@api_bp.post("/admin/nhp-memory/retry")
def admin_retry_nhp_memory():
    """Retry pending or failed durable NHP memory jobs."""

    if not _admin_authorized():
        return _admin_forbidden()
    payload = request.get_json(silent=True) or {}
    memory_ids = payload.get("memory_ids")
    if memory_ids is None and payload.get("memory_id"):
        memory_ids = [payload.get("memory_id")]
    ids = [str(item) for item in list(memory_ids or []) if str(item).strip()]
    return jsonify(current_app.extensions["game_service"].admin_retry_nhp_memory(ids or None))
