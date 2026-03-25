"""Flask application factory for standalone Clue gameplay."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urljoin

from flask import Flask

from clue_storage import ClueRepository
from clue_web.runtime import GameService


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip()
    return raw or "/"


def _aix_page_url(base_url: str, path: str) -> str:
    base = _normalize_base_url(base_url)
    if base == "/":
        return path
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def create_app(config: dict | None = None) -> Flask:
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "data"
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_mapping(
        SECRET_KEY=os.getenv("CLUE_SECRET_KEY", "clue-dev-secret-key"),
        DATABASE_URL=os.getenv("CLUE_DATABASE_URL", ""),
        DB_PATH=os.getenv("CLUE_DB_PATH", str(data_dir / "clue.db")),
        AIX_HUB_URL=os.getenv("AIX_HUB_URL", "/"),
        INTERNAL_WORKER_TOKEN=os.getenv("CLUE_INTERNAL_WORKER_TOKEN", ""),
        APP_BASE_PATH=os.getenv("APP_BASE_PATH", ""),
    )
    if config:
        app.config.update(config)

    db_target = app.config["DATABASE_URL"] or app.config["DB_PATH"]
    repository = ClueRepository(db_target)
    repository.init_schema()
    app.extensions["repository"] = repository
    app.extensions["game_service"] = GameService(repository, secret_key=str(app.config["SECRET_KEY"]))

    from clue_web.blueprints.api import api_bp
    from clue_web.blueprints.main import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    @app.context_processor
    def inject_template_globals() -> dict:
        hub_url = _normalize_base_url(app.config.get("AIX_HUB_URL", "/"))
        return {
            "aix_hub_url": hub_url,
            "aix_contact_url": _aix_page_url(hub_url, "/contact"),
            "aix_privacy_url": _aix_page_url(hub_url, "/privacy"),
            "aix_toc_url": _aix_page_url(hub_url, "/toc"),
        }

    return app
