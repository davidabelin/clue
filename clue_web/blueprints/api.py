"""JSON endpoints for gameplay, chat, notebook saves, and agent worker execution."""

from __future__ import annotations

import json
import time

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

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


def _since_event_index_from_request() -> int:
    """Parse and validate the optional incremental-events cursor."""

    raw = str(request.args.get("since", "0") or "0").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("Query parameter 'since' must be an integer.") from exc
    return max(value, 0)


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
    try:
        since_event_index = _since_event_index_from_request()
    except ValueError as exc:
        return _error(str(exc), 400)
    try:
        snapshot = current_app.extensions["game_service"].snapshot_for_token(token, since_event_index=since_event_index)
    except KeyError as exc:
        return _error(str(exc), 404)
    return jsonify(snapshot)


@api_bp.get("/games/current/stream")
def current_snapshot_stream():
    """Stream seat-filtered snapshots over SSE with cursor-based incremental updates."""

    token = _token_from_request()
    try:
        since_event_index = _since_event_index_from_request()
    except ValueError as exc:
        return _error(str(exc), 400)
    try:
        seat = current_app.extensions["game_service"].resolve_token(token)
    except KeyError as exc:
        return _error(str(exc), 404)

    poll_ms_raw = str(request.args.get("poll_ms", "900") or "900").strip()
    try:
        poll_ms = max(int(poll_ms_raw), 300)
    except ValueError:
        poll_ms = 900
    poll_seconds = poll_ms / 1000.0
    heartbeat_seconds = 10.0

    @stream_with_context
    def stream():
        cursor = since_event_index
        first_emit = True
        last_heartbeat = time.monotonic()

        yield "retry: 2000\n\n"
        while True:
            snapshot = current_app.extensions["game_service"].snapshot_for_seat(seat, since_event_index=cursor)
            next_cursor = int(snapshot.get("event_cursor", cursor) or cursor)
            should_emit = first_emit or next_cursor > cursor
            if should_emit:
                cursor = max(cursor, next_cursor)
                snapshot["event_cursor"] = cursor
                payload = json.dumps(snapshot, separators=(",", ":"))
                yield f"id: {cursor}\n"
                yield "event: snapshot\n"
                yield f"data: {payload}\n\n"
                first_emit = False
                last_heartbeat = time.monotonic()
            elif (time.monotonic() - last_heartbeat) >= heartbeat_seconds:
                yield ": keepalive\n\n"
                last_heartbeat = time.monotonic()
            time.sleep(poll_seconds)

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    return response


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
