"""Flask application factory for standalone Clue gameplay."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urljoin

from flask import Flask

from clue_core.version import CLUE_RELEASE_LABEL, CLUE_VERSION
from clue_storage import ClueRepository
from clue_web.runtime import GameService


def _read_secret_version(secret_version_name: str) -> str:
    """Read one Secret Manager version into plaintext app config."""

    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": secret_version_name})
    return response.payload.data.decode("utf-8")


def _resolve_secret_into_config(app: Flask, *, target_key: str, source_key: str) -> None:
    """Fill one config entry from Secret Manager when the direct value is empty."""

    if str(app.config.get(target_key, "")).strip():
        return
    secret_version_name = str(app.config.get(source_key, "")).strip()
    if not secret_version_name:
        return
    try:
        app.config[target_key] = _read_secret_version(secret_version_name)
    except Exception as exc:
        raise RuntimeError(
            f"Failed loading {target_key} from Secret Manager secret version '{secret_version_name}': {exc}"
        ) from exc


def _normalize_base_url(value: str) -> str:
    """Normalize empty AIX link targets to the root path."""

    raw = str(value or "").strip()
    return raw or "/"


def _aix_page_url(base_url: str, path: str) -> str:
    """Compose one AIX-owned chrome link from the configured hub base URL."""

    base = _normalize_base_url(base_url)
    if base == "/":
        return path
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def _static_asset_token(static_root: Path, *, version: str) -> str:
    """Build a lightweight cache-busting token for bundled static assets."""

    latest_mtime = 0
    for path in static_root.rglob("*"):
        if path.is_file():
            latest_mtime = max(latest_mtime, int(path.stat().st_mtime))
    return f"{version}-{latest_mtime}"


def create_app(config: dict | None = None) -> Flask:
    """Create the standalone Clue Flask app and wire storage, routes, and chrome links.

    The app factory is also the central configuration bridge for v1.7.4. It
    keeps deployment defaults explicit so future maintainers can see which knobs
    belong to the OpenAI seat runtime versus the core Flask or storage layers,
    and where Secret Manager-backed values may override local defaults.
    """

    root = Path(__file__).resolve().parents[1]
    data_dir = root / "data"
    app = Flask(__name__, template_folder="templates", static_folder="static")
    static_token = _static_asset_token(root / "clue_web" / "static", version=CLUE_VERSION)
    app.config.from_mapping(
        CLUE_VERSION=CLUE_VERSION,
        CLUE_RELEASE_LABEL=CLUE_RELEASE_LABEL,
        CLUE_STATIC_TOKEN=static_token,
        SECRET_KEY=os.getenv("CLUE_SECRET_KEY", "clue-dev-secret-key"),
        DATABASE_URL=os.getenv("CLUE_DATABASE_URL", ""),
        DATABASE_URL_SECRET=os.getenv("CLUE_DATABASE_URL_SECRET", ""),
        DB_PATH=os.getenv("CLUE_DB_PATH", str(data_dir / "clue.db")),
        AIX_HUB_URL=os.getenv("AIX_HUB_URL", "/"),
        INTERNAL_WORKER_TOKEN=os.getenv("CLUE_INTERNAL_WORKER_TOKEN", ""),
        APP_BASE_PATH=os.getenv("APP_BASE_PATH", ""),
        CLUE_LLM_MODEL=os.getenv("CLUE_LLM_MODEL", "gpt-5.4-mini-2026-03-17"),
        CLUE_LLM_REASONING_EFFORT=os.getenv("CLUE_LLM_REASONING_EFFORT", "medium"),
        CLUE_LLM_TIMEOUT_SECONDS=os.getenv("CLUE_LLM_TIMEOUT_SECONDS", "12"),
        CLUE_LLM_MAX_TOOL_CALLS=os.getenv("CLUE_LLM_MAX_TOOL_CALLS", "6"),
        CLUE_AGENT_MAX_TURNS=os.getenv("CLUE_AGENT_MAX_TURNS", "8"),
        CLUE_AGENT_TRACING_ENABLED=os.getenv("CLUE_AGENT_TRACING_ENABLED", "0"),
        CLUE_AGENT_TRACE_INCLUDE_SENSITIVE_DATA=os.getenv("CLUE_AGENT_TRACE_INCLUDE_SENSITIVE_DATA", "0"),
        CLUE_AGENT_SESSION_TTL_SECONDS=os.getenv("CLUE_AGENT_SESSION_TTL_SECONDS", "900"),
        CLUE_AGENT_SESSION_DB_PATH=os.getenv("CLUE_AGENT_SESSION_DB_PATH", str(data_dir / "clue_agent_sessions.db")),
        CLUE_AGENT_SESSION_ENCRYPTION_KEY=os.getenv("CLUE_AGENT_SESSION_ENCRYPTION_KEY", ""),
        CLUE_AGENT_EVAL_EXPORT_ENABLED=os.getenv("CLUE_AGENT_EVAL_EXPORT_ENABLED", "0"),
    )
    if config:
        app.config.update(config)

    _resolve_secret_into_config(app, target_key="DATABASE_URL", source_key="DATABASE_URL_SECRET")
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
        """Expose shared chrome and release metadata to the Clue templates."""

        hub_url = _normalize_base_url(app.config.get("AIX_HUB_URL", "/"))
        return {
            "clue_version": app.config["CLUE_VERSION"],
            "clue_release_label": app.config["CLUE_RELEASE_LABEL"],
            "clue_static_token": app.config["CLUE_STATIC_TOKEN"],
            "aix_hub_url": hub_url,
            "aix_contact_url": _aix_page_url(hub_url, "/contact"),
            "aix_privacy_url": _aix_page_url(hub_url, "/privacy"),
            "aix_toc_url": _aix_page_url(hub_url, "/toc"),
        }

    return app
