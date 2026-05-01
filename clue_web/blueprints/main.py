"""HTML routes for the standalone Clue web app."""

from __future__ import annotations

from flask import Blueprint, current_app, redirect, render_template, request, url_for

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def home() -> str:
    """Render the create-game landing page."""

    return render_template("pages/home.html", title="Clue")


@main_bp.get("/join/<token>")
def join_by_token(token: str):
    """Mark the seat as seen and redirect the invite link onto the game page."""

    current_app.extensions["game_service"].join_by_token(token)
    return redirect(url_for("main.game_page", token=token))


@main_bp.get("/game")
def game_page() -> str:
    """Render the seat-specific game view when a valid token is present."""

    token = str(request.args.get("token", "")).strip()
    if not token:
        return redirect(url_for("main.home"))
    seat = current_app.extensions["game_service"].join_by_token(token)
    return render_template("pages/game.html", title="Clue Table", seat=seat, seat_token=token)


def _admin_token_is_valid(token: str) -> bool:
    """Return whether one provided Administrator Mode token matches config."""

    expected = str(current_app.config.get("CLUE_ADMIN_TOKEN", "")).strip()
    return bool(expected and str(token or "").strip() == expected)


def _admin_forbidden():
    """Return the standard protected-admin route response."""

    return (
        render_template(
            "pages/admin_entry.html",
            title="Clue Superplayer Admin",
            error="Administrator token required.",
            admin_configured=bool(str(current_app.config.get("CLUE_ADMIN_TOKEN", "")).strip()),
        ),
        403,
    )


def _admin_form_payload() -> dict[str, str]:
    """Read a form while letting later duplicate field values win."""

    return {key: values[-1] for key in request.form for values in [request.form.getlist(key)] if values}


@main_bp.get("/admin")
def admin_page():
    """Render the protected Superplayer Administrator Mode dashboard."""

    token = str(request.args.get("admin_token", "")).strip()
    if not token:
        return render_template(
            "pages/admin_entry.html",
            title="Clue Superplayer Admin",
            error="",
            admin_configured=bool(str(current_app.config.get("CLUE_ADMIN_TOKEN", "")).strip()),
        )
    if not _admin_token_is_valid(token):
        return _admin_forbidden()
    dashboard = current_app.extensions["game_service"].admin_dashboard()
    return render_template(
        "pages/admin.html",
        title="Clue Superplayer Admin",
        dashboard=dashboard,
        admin_token=token,
        notice=str(request.args.get("notice", "") or ""),
    )


@main_bp.get("/admin/games/<game_id>")
def admin_game_page(game_id: str):
    """Render one full saved-game inspection page for Administrator Mode."""

    token = str(request.args.get("admin_token", "")).strip()
    if not _admin_token_is_valid(token):
        return _admin_forbidden()
    try:
        game = current_app.extensions["game_service"].admin_game_review(game_id)
    except KeyError:
        return "Saved game not found.", 404
    return render_template("pages/admin_game.html", title=f"Clue Admin: {game['title']}", game=game, admin_token=token)


@main_bp.post("/admin/games/<game_id>/terminate")
def admin_terminate_game_form(game_id: str):
    """Admin postback for marking a stale active game as terminated."""

    payload = _admin_form_payload()
    token = str(payload.get("admin_token", "")).strip()
    if not _admin_token_is_valid(token):
        return _admin_forbidden()
    try:
        current_app.extensions["game_service"].admin_terminate_game(game_id)
    except KeyError:
        return "Saved game not found.", 404
    return redirect(url_for("main.admin_page", admin_token=token, notice="Game terminated."))


@main_bp.post("/admin/games/<game_id>/delete")
def admin_delete_game_form(game_id: str):
    """Admin postback for permanently deleting a saved game."""

    payload = _admin_form_payload()
    token = str(payload.get("admin_token", "")).strip()
    if not _admin_token_is_valid(token):
        return _admin_forbidden()
    try:
        current_app.extensions["game_service"].admin_delete_game(game_id)
    except KeyError:
        return "Saved game not found.", 404
    return redirect(url_for("main.admin_page", admin_token=token, notice="Game deleted."))


@main_bp.post("/admin/runtime-settings")
def admin_runtime_settings_form():
    """Update process-local optional-chat settings from the admin dashboard."""

    payload = _admin_form_payload()
    token = str(payload.get("admin_token", "")).strip()
    if not _admin_token_is_valid(token):
        return _admin_forbidden()
    current_app.extensions["game_service"].update_admin_runtime_settings(payload)
    return redirect(url_for("main.admin_page", admin_token=token, notice="Runtime settings updated."))


@main_bp.post("/admin/nhp-memory/retry")
def admin_retry_memory_form():
    """Retry pending or selected durable memory jobs from the admin dashboard."""

    payload = _admin_form_payload()
    token = str(payload.get("admin_token", "")).strip()
    if not _admin_token_is_valid(token):
        return _admin_forbidden()
    memory_ids = [item for item in request.form.getlist("memory_id") if str(item).strip()]
    result = current_app.extensions["game_service"].admin_retry_nhp_memory(memory_ids or None)
    return redirect(
        url_for(
            "main.admin_page",
            admin_token=token,
            notice=f"Memory retry attempted {int(result.get('attempted', 0))} job(s).",
        )
    )
