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


@main_bp.get("/admin")
def admin_page():
    """Render the protected plain Administrator Mode dashboard."""

    token = str(request.args.get("admin_token", "")).strip()
    if not _admin_token_is_valid(token):
        return "Administrator token required.", 403
    dashboard = current_app.extensions["game_service"].admin_dashboard()
    return render_template("pages/admin.html", title="Clue Administrator", dashboard=dashboard, admin_token=token)
