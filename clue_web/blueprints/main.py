"""HTML routes for the standalone Clue web app."""

from __future__ import annotations

from flask import Blueprint, current_app, redirect, render_template, request, url_for

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def home() -> str:
    return render_template("pages/home.html", title="Clue")


@main_bp.get("/join/<token>")
def join_by_token(token: str):
    current_app.extensions["game_service"].join_by_token(token)
    return redirect(url_for("main.game_page", token=token))


@main_bp.get("/game")
def game_page() -> str:
    token = str(request.args.get("token", "")).strip()
    if not token:
        return redirect(url_for("main.home"))
    seat = current_app.extensions["game_service"].join_by_token(token)
    return render_template("pages/game.html", title="Clue Table", seat=seat, seat_token=token)
