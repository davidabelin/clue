"""Repository layer for Clue game state, seats, tokens, and event history.

The repository keeps SQL concerns thin and boring on purpose. Gameplay semantics
live in Python state objects; this layer is responsible for durable storage,
token lookup, and event ordering across SQLite and PostgreSQL targets.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from threading import RLock
from typing import Any
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, MappingResult

from clue_core.events import utcnow_iso


NHP_MEMORY_STATUSES = {"pending", "ready", "failed"}
"""Persisted lifecycle states for durable non-human-player memory jobs."""


def normalize_player_identity(value: str) -> str:
    """Normalize one human-facing display name for cross-game memory lookup."""

    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _looks_like_database_url(value: str) -> bool:
    """Best-effort check for SQLAlchemy-style connection URLs."""

    return "://" in str(value)


def _to_sqlite_url(path_value: str) -> str:
    """Convert one filesystem path into a SQLite SQLAlchemy URL."""

    path = Path(path_value).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _memory_job_id(game_id: str, seat_id: str) -> str:
    """Return the stable durable-memory job id for one completed game seat."""

    return f"{str(game_id)}:{str(seat_id)}"


def _relationship_id(agent_identity: str, target_kind: str, target_identity: str) -> str:
    """Return the stable durable relationship id for one agent-target pair."""

    return f"{str(agent_identity)}|{str(target_kind)}|{str(target_identity)}"


def _note_id() -> str:
    """Return one sortable-ish durable NHP note id."""

    return f"note_{uuid.uuid4().hex}"


def _clamp_int(value: Any, *, minimum: int, maximum: int) -> int:
    """Clamp an integer-like value into an inclusive range."""

    try:
        return min(max(int(value), minimum), maximum)
    except (TypeError, ValueError):
        return minimum


class ClueRepository:
    """Persistence facade for standalone and AIX-mounted Clue gameplay.

    The repository persists opaque JSON payloads for config, setup, state, and
    notebooks rather than trying to model the full rules system relationally.
    That keeps the domain logic centralized in the code paths that already own
    privacy, legality, and event semantics.
    """

    def __init__(self, db_target: str) -> None:
        """Initialize the SQLAlchemy engine for SQLite or PostgreSQL storage."""

        target = str(db_target).strip()
        if not target:
            raise ValueError("Database target must not be empty.")
        self.db_target = target
        self.db_url = target if _looks_like_database_url(target) else _to_sqlite_url(target)
        connect_args = {"check_same_thread": False} if self.db_url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(self.db_url, future=True, pool_pre_ping=True, connect_args=connect_args)
        self._dialect = self.engine.dialect.name
        self._lock = RLock()

    @staticmethod
    def _first_or_none(rows: MappingResult) -> dict[str, Any] | None:
        """Return the first mapping row as a plain dict, or ``None`` when empty."""

        row = rows.first()
        return dict(row) if row is not None else None

    def _run_script(self, script: str) -> None:
        """Execute a semicolon-delimited schema script statement-by-statement."""

        statements = [part.strip() for part in script.split(";") if part.strip()]
        with self.engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))

    def init_schema(self) -> None:
        """Create the required tables and indexes for the active SQL dialect.

        Schema creation is idempotent so local development, tests, and deployed
        startup can all call it safely without a separate migration step.
        """

        sqlite_schema = """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS games (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                config_json TEXT NOT NULL,
                setup_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS seats (
                id TEXT PRIMARY KEY,
                game_id TEXT NOT NULL,
                seat_key TEXT,
                display_name TEXT NOT NULL,
                character_name TEXT NOT NULL,
                seat_kind TEXT NOT NULL,
                agent_model TEXT NOT NULL DEFAULT '',
                notebook_json TEXT NOT NULL DEFAULT '{}',
                first_seen_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id)
            );

            CREATE TABLE IF NOT EXISTS seat_tokens (
                token TEXT PRIMARY KEY,
                game_id TEXT NOT NULL,
                seat_id TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id),
                FOREIGN KEY(seat_id) REFERENCES seats(id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                event_index INTEGER NOT NULL,
                visibility TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id)
            );

            CREATE TABLE IF NOT EXISTS nhp_memory (
                id TEXT PRIMARY KEY,
                agent_identity TEXT NOT NULL,
                status TEXT NOT NULL,
                source_game_id TEXT NOT NULL,
                source_seat_id TEXT NOT NULL,
                source_character TEXT NOT NULL,
                source_display_name TEXT NOT NULL,
                summary_json TEXT NOT NULL DEFAULT '{}',
                model_meta_json TEXT NOT NULL DEFAULT '{}',
                failure_reason TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(source_game_id) REFERENCES games(id),
                UNIQUE(source_game_id, source_seat_id)
            );

            CREATE TABLE IF NOT EXISTS nhp_relationships (
                id TEXT PRIMARY KEY,
                agent_identity TEXT NOT NULL,
                target_kind TEXT NOT NULL,
                target_identity TEXT NOT NULL,
                target_display_name TEXT NOT NULL DEFAULT '',
                affinity INTEGER NOT NULL DEFAULT 0,
                trust INTEGER NOT NULL DEFAULT 0,
                friction INTEGER NOT NULL DEFAULT 0,
                notes_json TEXT NOT NULL DEFAULT '[]',
                last_source_game_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(agent_identity, target_kind, target_identity)
            );

            CREATE TABLE IF NOT EXISTS nhp_notes (
                id TEXT PRIMARY KEY,
                note_kind TEXT NOT NULL,
                tool_name TEXT NOT NULL DEFAULT '',
                agent_identity TEXT NOT NULL,
                source_game_id TEXT NOT NULL,
                source_seat_id TEXT NOT NULL,
                target_kind TEXT NOT NULL DEFAULT '',
                target_identity TEXT NOT NULL DEFAULT '',
                target_display_name TEXT NOT NULL DEFAULT '',
                note_text TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_game_id) REFERENCES games(id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_game_index ON events(game_id, event_index);
            CREATE INDEX IF NOT EXISTS idx_seats_game ON seats(game_id);
            CREATE INDEX IF NOT EXISTS idx_events_game_id ON events(game_id, id);
            CREATE INDEX IF NOT EXISTS idx_tokens_game ON seat_tokens(game_id);
            CREATE INDEX IF NOT EXISTS idx_nhp_memory_agent ON nhp_memory(agent_identity, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_nhp_memory_status ON nhp_memory(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_nhp_relationships_agent ON nhp_relationships(agent_identity);
            CREATE INDEX IF NOT EXISTS idx_nhp_notes_agent ON nhp_notes(agent_identity, created_at);
            CREATE INDEX IF NOT EXISTS idx_nhp_notes_source ON nhp_notes(source_game_id, source_seat_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_nhp_notes_target ON nhp_notes(target_kind, target_identity, created_at);
        """

        postgres_schema = """
            CREATE TABLE IF NOT EXISTS games (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                config_json TEXT NOT NULL,
                setup_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS seats (
                id TEXT PRIMARY KEY,
                game_id TEXT NOT NULL REFERENCES games(id),
                seat_key TEXT,
                display_name TEXT NOT NULL,
                character_name TEXT NOT NULL,
                seat_kind TEXT NOT NULL,
                agent_model TEXT NOT NULL DEFAULT '',
                notebook_json TEXT NOT NULL DEFAULT '{}',
                first_seen_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS seat_tokens (
                token TEXT PRIMARY KEY,
                game_id TEXT NOT NULL REFERENCES games(id),
                seat_id TEXT NOT NULL REFERENCES seats(id),
                issued_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id BIGSERIAL PRIMARY KEY,
                game_id TEXT NOT NULL REFERENCES games(id),
                event_index INTEGER NOT NULL,
                visibility TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS nhp_memory (
                id TEXT PRIMARY KEY,
                agent_identity TEXT NOT NULL,
                status TEXT NOT NULL,
                source_game_id TEXT NOT NULL REFERENCES games(id),
                source_seat_id TEXT NOT NULL,
                source_character TEXT NOT NULL,
                source_display_name TEXT NOT NULL,
                summary_json TEXT NOT NULL DEFAULT '{}',
                model_meta_json TEXT NOT NULL DEFAULT '{}',
                failure_reason TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_game_id, source_seat_id)
            );

            CREATE TABLE IF NOT EXISTS nhp_relationships (
                id TEXT PRIMARY KEY,
                agent_identity TEXT NOT NULL,
                target_kind TEXT NOT NULL,
                target_identity TEXT NOT NULL,
                target_display_name TEXT NOT NULL DEFAULT '',
                affinity INTEGER NOT NULL DEFAULT 0,
                trust INTEGER NOT NULL DEFAULT 0,
                friction INTEGER NOT NULL DEFAULT 0,
                notes_json TEXT NOT NULL DEFAULT '[]',
                last_source_game_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(agent_identity, target_kind, target_identity)
            );

            CREATE TABLE IF NOT EXISTS nhp_notes (
                id TEXT PRIMARY KEY,
                note_kind TEXT NOT NULL,
                tool_name TEXT NOT NULL DEFAULT '',
                agent_identity TEXT NOT NULL,
                source_game_id TEXT NOT NULL REFERENCES games(id),
                source_seat_id TEXT NOT NULL,
                target_kind TEXT NOT NULL DEFAULT '',
                target_identity TEXT NOT NULL DEFAULT '',
                target_display_name TEXT NOT NULL DEFAULT '',
                note_text TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_game_index ON events(game_id, event_index);
            CREATE INDEX IF NOT EXISTS idx_seats_game ON seats(game_id);
            CREATE INDEX IF NOT EXISTS idx_events_game_id ON events(game_id, id);
            CREATE INDEX IF NOT EXISTS idx_tokens_game ON seat_tokens(game_id);
            CREATE INDEX IF NOT EXISTS idx_nhp_memory_agent ON nhp_memory(agent_identity, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_nhp_memory_status ON nhp_memory(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_nhp_relationships_agent ON nhp_relationships(agent_identity);
            CREATE INDEX IF NOT EXISTS idx_nhp_notes_agent ON nhp_notes(agent_identity, created_at);
            CREATE INDEX IF NOT EXISTS idx_nhp_notes_source ON nhp_notes(source_game_id, source_seat_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_nhp_notes_target ON nhp_notes(target_kind, target_identity, created_at);
        """
        self._run_script(sqlite_schema if self._dialect == "sqlite" else postgres_schema)
        self._ensure_seat_key_column()

    def _ensure_seat_key_column(self) -> None:
        """Backfill the stable seat-key column used by token lookups across games."""

        if self._dialect == "sqlite":
            query = text("PRAGMA table_info(seats)")
            key_name = "name"
        else:
            query = text(
                """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_name = 'seats'
                """
            )
            key_name = "name"
        with self.engine.begin() as conn:
            columns = {str(row[key_name]) for row in conn.execute(query).mappings()}
            if "seat_key" not in columns:
                conn.execute(text("ALTER TABLE seats ADD COLUMN seat_key TEXT"))
            conn.execute(text("UPDATE seats SET seat_key = COALESCE(NULLIF(seat_key, ''), id) WHERE seat_key IS NULL OR seat_key = ''"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_seats_game_key ON seats(game_id, seat_key)"))

    def create_game(
        self,
        *,
        game_id: str,
        title: str,
        config: dict[str, Any],
        setup: dict[str, Any],
        state: dict[str, Any],
        seats: list[dict[str, Any]],
        seat_tokens: list[dict[str, str]],
        events: list[dict[str, Any]],
    ) -> None:
        """Persist one freshly created game plus seats, tokens, and opening events.

        The initial write is transactional so game state, seat rows, invite
        tokens, and opening events either appear together or not at all.
        """

        created_at = utcnow_iso()
        with self._lock, self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO games (id, title, status, config_json, setup_json, state_json, created_at, updated_at)
                    VALUES (:id, :title, :status, :config_json, :setup_json, :state_json, :created_at, :updated_at)
                    """
                ),
                {
                    "id": game_id,
                    "title": title,
                    "status": str(state["status"]),
                    "config_json": json.dumps(config),
                    "setup_json": json.dumps(setup),
                    "state_json": json.dumps(state),
                    "created_at": created_at,
                    "updated_at": created_at,
                },
            )
            seat_row_ids: dict[str, str] = {}
            for seat in seats:
                seat_row_id = f"{game_id}:{seat['seat_id']}"
                seat_row_ids[seat["seat_id"]] = seat_row_id
                conn.execute(
                    text(
                        """
                        INSERT INTO seats (
                            id, game_id, seat_key, display_name, character_name, seat_kind, agent_model,
                            notebook_json, first_seen_at, created_at, updated_at
                        )
                        VALUES (
                            :id, :game_id, :seat_key, :display_name, :character_name, :seat_kind, :agent_model,
                            :notebook_json, NULL, :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "id": seat_row_id,
                        "game_id": game_id,
                        "seat_key": seat["seat_id"],
                        "display_name": seat["display_name"],
                        "character_name": seat["character"],
                        "seat_kind": seat["seat_kind"],
                        "agent_model": seat.get("agent_model", ""),
                        "notebook_json": json.dumps(seat.get("notebook") or {}),
                        "created_at": created_at,
                        "updated_at": created_at,
                    },
                )
            for token_row in seat_tokens:
                conn.execute(
                    text(
                        """
                        INSERT INTO seat_tokens (token, game_id, seat_id, issued_at)
                        VALUES (:token, :game_id, :seat_id, :issued_at)
                        """
                    ),
                    {
                        "token": token_row["token"],
                        "game_id": game_id,
                        "seat_id": seat_row_ids[token_row["seat_id"]],
                        "issued_at": created_at,
                    },
                )
            self._insert_events(conn, game_id=game_id, events=events)

    def _insert_events(self, conn, *, game_id: str, events: list[dict[str, Any]]) -> None:
        """Append ordered event rows to the event log for one game."""

        current_index = self.next_event_index(game_id, conn=conn)
        for offset, event in enumerate(events, start=1):
            conn.execute(
                text(
                    """
                    INSERT INTO events (game_id, event_index, visibility, event_type, message, payload_json, created_at)
                    VALUES (:game_id, :event_index, :visibility, :event_type, :message, :payload_json, :created_at)
                    """
                ),
                {
                    "game_id": game_id,
                    "event_index": current_index + offset,
                    "visibility": event["visibility"],
                    "event_type": event["event_type"],
                    "message": event["message"],
                    "payload_json": json.dumps(event.get("payload") or {}),
                    "created_at": event["created_at"],
                },
            )

    def next_event_index(self, game_id: str, *, conn=None) -> int:
        """Return the current highest event index for one game."""

        owns_connection = conn is None
        if owns_connection:
            context = self.engine.begin()
            conn = context.__enter__()
        try:
            row = self._first_or_none(
                conn.execute(text("SELECT COALESCE(MAX(event_index), 0) AS max_index FROM events WHERE game_id = :game_id"), {"game_id": game_id}).mappings()
            )
            return int(row["max_index"] if row is not None else 0)
        finally:
            if owns_connection:
                context.__exit__(None, None, None)

    def get_game_record(self, game_id: str) -> dict[str, Any] | None:
        """Load the persisted config/setup/state bundle for one game id."""

        with self.engine.begin() as conn:
            row = self._first_or_none(
                conn.execute(text("SELECT * FROM games WHERE id = :game_id"), {"game_id": game_id}).mappings()
            )
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "config": json.loads(row["config_json"]),
            "setup": json.loads(row["setup_json"]),
            "state": json.loads(row["state_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_state(self, game_id: str) -> dict[str, Any]:
        """Return the mutable gameplay state payload for one game."""

        row = self.get_game_record(game_id)
        if row is None:
            raise KeyError(f"Unknown game: {game_id}")
        return dict(row["state"])

    def save_state_and_events(self, game_id: str, *, state: dict[str, Any], events: list[dict[str, Any]]) -> None:
        """Persist one post-action state snapshot and its newly emitted events.

        Callers are expected to hand in a state snapshot that already reflects
        the applied action. Event ordering is preserved by appending against the
        same transaction that updates the game row.
        """

        updated_at = utcnow_iso()
        with self._lock, self.engine.begin() as conn:
            conn.execute(
                text("UPDATE games SET status = :status, state_json = :state_json, updated_at = :updated_at WHERE id = :game_id"),
                {
                    "game_id": game_id,
                    "status": str(state["status"]),
                    "state_json": json.dumps(state),
                    "updated_at": updated_at,
                },
            )
            self._insert_events(conn, game_id=game_id, events=events)

    def list_seats(self, game_id: str) -> list[dict[str, Any]]:
        """Return the seat rows for one game in creation order."""

        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT * FROM seats WHERE game_id = :game_id ORDER BY created_at, id"),
                {"game_id": game_id},
            ).mappings()
            return [
                {
                    "seat_id": row["seat_key"] or row["id"],
                    "game_id": row["game_id"],
                    "display_name": row["display_name"],
                    "character": row["character_name"],
                    "seat_kind": row["seat_kind"],
                    "agent_model": row["agent_model"],
                    "notebook": json.loads(row["notebook_json"] or "{}"),
                    "first_seen_at": row["first_seen_at"],
                }
                for row in rows
            ]

    def get_seat_by_token(self, token: str) -> dict[str, Any] | None:
        """Resolve one signed seat token to its persisted seat context."""

        with self.engine.begin() as conn:
            row = self._first_or_none(
                conn.execute(
                    text(
                        """
                        SELECT t.token, t.game_id, t.seat_id AS seat_row_id, s.seat_key, s.display_name, s.character_name, s.seat_kind,
                               s.agent_model, s.notebook_json, s.first_seen_at
                        FROM seat_tokens AS t
                        JOIN seats AS s ON s.id = t.seat_id
                        WHERE t.token = :token
                        """
                    ),
                    {"token": token},
                ).mappings()
            )
        if row is None:
            return None
        return {
            "token": row["token"],
            "game_id": row["game_id"],
            "seat_id": row["seat_key"] or row["seat_row_id"],
            "display_name": row["display_name"],
            "character": row["character_name"],
            "seat_kind": row["seat_kind"],
            "agent_model": row["agent_model"],
            "notebook": json.loads(row["notebook_json"] or "{}"),
            "first_seen_at": row["first_seen_at"],
        }

    def mark_seat_seen(self, game_id: str, seat_id: str) -> None:
        """Record first-join time for one seat without overwriting earlier joins."""

        with self._lock, self.engine.begin() as conn:
            now = utcnow_iso()
            conn.execute(
                text(
                    """
                    UPDATE seats
                    SET first_seen_at = COALESCE(first_seen_at, :now), updated_at = :now
                    WHERE game_id = :game_id AND seat_key = :seat_id
                    """
                ),
                {"game_id": game_id, "seat_id": seat_id, "now": now},
            )

    def update_notebook(self, game_id: str, seat_id: str, notebook: dict[str, Any]) -> None:
        """Persist the private notebook payload for one seat."""

        with self._lock, self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE seats
                    SET notebook_json = :notebook_json, updated_at = :updated_at
                    WHERE game_id = :game_id AND seat_key = :seat_id
                    """
                ),
                {
                    "game_id": game_id,
                    "seat_id": seat_id,
                    "notebook_json": json.dumps(notebook),
                    "updated_at": utcnow_iso(),
                },
            )

    def visible_events(self, game_id: str, *, seat_id: str, since_event_index: int = 0) -> list[dict[str, Any]]:
        """Return public plus seat-private events visible to one seat after a cursor.

        Visibility is enforced in SQL so callers can build browser and agent
        snapshots from the same filtered event stream.
        """

        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, event_index, visibility, event_type, message, payload_json, created_at
                    FROM events
                    WHERE game_id = :game_id
                      AND event_index > :since_event_index
                      AND (visibility = 'public' OR visibility = :seat_visibility)
                    ORDER BY event_index
                    """
                ),
                {
                    "game_id": game_id,
                    "since_event_index": since_event_index,
                    "seat_visibility": f"seat:{seat_id}",
                },
            ).mappings()
            return [
                {
                    "id": int(row["id"]),
                    "event_index": int(row["event_index"]),
                    "visibility": row["visibility"],
                    "event_type": row["event_type"],
                    "message": row["message"],
                    "payload": json.loads(row["payload_json"] or "{}"),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def public_events(self, game_id: str, *, since_event_index: int = 0) -> list[dict[str, Any]]:
        """Return the public event stream for one game after a cursor."""

        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, event_index, visibility, event_type, message, payload_json, created_at
                    FROM events
                    WHERE game_id = :game_id
                      AND event_index > :since_event_index
                      AND visibility = 'public'
                    ORDER BY event_index
                    """
                ),
                {
                    "game_id": game_id,
                    "since_event_index": since_event_index,
                },
            ).mappings()
            return [
                {
                    "id": int(row["id"]),
                    "event_index": int(row["event_index"]),
                    "visibility": row["visibility"],
                    "event_type": row["event_type"],
                    "message": row["message"],
                    "payload": json.loads(row["payload_json"] or "{}"),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    @staticmethod
    def _memory_row(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize one durable NHP memory database row into app-facing shape."""

        return {
            "id": row["id"],
            "agent_identity": row["agent_identity"],
            "status": row["status"],
            "source_game_id": row["source_game_id"],
            "source_seat_id": row["source_seat_id"],
            "source_character": row["source_character"],
            "source_display_name": row["source_display_name"],
            "summary": json.loads(row["summary_json"] or "{}"),
            "model_meta": json.loads(row["model_meta_json"] or "{}"),
            "failure_reason": row["failure_reason"],
            "retry_count": int(row["retry_count"] or 0),
            "completed_at": row["completed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _relationship_row(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize one durable relationship database row into app-facing shape."""

        return {
            "id": row["id"],
            "agent_identity": row["agent_identity"],
            "target_kind": row["target_kind"],
            "target_identity": row["target_identity"],
            "target_display_name": row["target_display_name"],
            "affinity": int(row["affinity"] or 0),
            "trust": int(row["trust"] or 0),
            "friction": int(row["friction"] or 0),
            "notes": json.loads(row["notes_json"] or "[]"),
            "last_source_game_id": row["last_source_game_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _note_row(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize one durable NHP note or tool-audit row into app-facing shape."""

        return {
            "id": row["id"],
            "note_kind": row["note_kind"],
            "tool_name": row["tool_name"],
            "agent_identity": row["agent_identity"],
            "source_game_id": row["source_game_id"],
            "source_seat_id": row["source_seat_id"],
            "target_kind": row["target_kind"],
            "target_identity": row["target_identity"],
            "target_display_name": row["target_display_name"],
            "note_text": row["note_text"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "created_at": row["created_at"],
        }

    def list_games(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent saved games for Administrator Mode."""

        safe_limit = min(max(int(limit), 1), 250)
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, title, status, created_at, updated_at
                    FROM games
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": safe_limit},
            ).mappings()
            return [dict(row) for row in rows]

    def events_for_game(self, game_id: str) -> list[dict[str, Any]]:
        """Return the complete event stream for an admin-authorized game detail view."""

        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, event_index, visibility, event_type, message, payload_json, created_at
                    FROM events
                    WHERE game_id = :game_id
                    ORDER BY event_index
                    """
                ),
                {"game_id": game_id},
            ).mappings()
            return [
                {
                    "id": int(row["id"]),
                    "event_index": int(row["event_index"]),
                    "visibility": row["visibility"],
                    "event_type": row["event_type"],
                    "message": row["message"],
                    "payload": json.loads(row["payload_json"] or "{}"),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def ensure_nhp_memory_job(self, *, game_id: str, seat_id: str, character: str, display_name: str) -> dict[str, Any]:
        """Create or return the durable memory job for one completed NHP seat."""

        memory_id = _memory_job_id(game_id, seat_id)
        now = utcnow_iso()
        with self._lock, self.engine.begin() as conn:
            existing = self._first_or_none(
                conn.execute(text("SELECT * FROM nhp_memory WHERE id = :id"), {"id": memory_id}).mappings()
            )
            if existing is None:
                conn.execute(
                    text(
                        """
                        INSERT INTO nhp_memory (
                            id, agent_identity, status, source_game_id, source_seat_id,
                            source_character, source_display_name, summary_json, model_meta_json,
                            failure_reason, retry_count, completed_at, created_at, updated_at
                        )
                        VALUES (
                            :id, :agent_identity, 'pending', :source_game_id, :source_seat_id,
                            :source_character, :source_display_name, '{}', '{}',
                            '', 0, NULL, :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "id": memory_id,
                        "agent_identity": str(character),
                        "source_game_id": str(game_id),
                        "source_seat_id": str(seat_id),
                        "source_character": str(character),
                        "source_display_name": str(display_name),
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                existing = self._first_or_none(
                    conn.execute(text("SELECT * FROM nhp_memory WHERE id = :id"), {"id": memory_id}).mappings()
                )
        if existing is None:
            raise RuntimeError(f"Could not create durable NHP memory job: {memory_id}")
        return self._memory_row(existing)

    def get_nhp_memory_job(self, memory_id: str) -> dict[str, Any] | None:
        """Return one durable NHP memory job by id."""

        with self.engine.begin() as conn:
            row = self._first_or_none(
                conn.execute(text("SELECT * FROM nhp_memory WHERE id = :id"), {"id": str(memory_id)}).mappings()
            )
        return self._memory_row(row) if row is not None else None

    def list_nhp_memory(self, *, status: str = "", agent_identity: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Return durable NHP memory rows for Administrator Mode and runtime loading."""

        clauses: list[str] = []
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
        if str(status).strip():
            clauses.append("status = :status")
            params["status"] = str(status).strip()
        if str(agent_identity).strip():
            clauses.append("agent_identity = :agent_identity")
            params["agent_identity"] = str(agent_identity).strip()
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT *
                    FROM nhp_memory
                    {where}
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings()
            return [self._memory_row(dict(row)) for row in rows]

    def ready_nhp_memory_for_agent(self, agent_identity: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Return the recent ready memory summaries for one canonical NHP identity."""

        return self.list_nhp_memory(status="ready", agent_identity=agent_identity, limit=limit)

    def list_pending_nhp_memory_jobs(self, *, include_failed: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        """Return queued durable memory jobs that still need an LLM summary."""

        statuses = ("'pending', 'failed'" if include_failed else "'pending'")
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT *
                    FROM nhp_memory
                    WHERE status IN ({statuses})
                    ORDER BY updated_at ASC, created_at ASC
                    LIMIT :limit
                    """
                ),
                {"limit": min(max(int(limit), 1), 500)},
            ).mappings()
            return [self._memory_row(dict(row)) for row in rows]

    def mark_nhp_memory_ready(self, memory_id: str, *, summary: dict[str, Any], model_meta: dict[str, Any]) -> dict[str, Any]:
        """Persist a completed LLM-authored durable memory summary."""

        now = utcnow_iso()
        with self._lock, self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE nhp_memory
                    SET status = 'ready',
                        summary_json = :summary_json,
                        model_meta_json = :model_meta_json,
                        failure_reason = '',
                        completed_at = :completed_at,
                        updated_at = :updated_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": str(memory_id),
                    "summary_json": json.dumps(summary),
                    "model_meta_json": json.dumps(model_meta),
                    "completed_at": now,
                    "updated_at": now,
                },
            )
        job = self.get_nhp_memory_job(memory_id)
        if job is None:
            raise KeyError(f"Unknown durable NHP memory job: {memory_id}")
        return job

    def mark_nhp_memory_failure(self, memory_id: str, *, reason: str, status: str = "failed") -> dict[str, Any]:
        """Record a failed or queued memory-summary attempt without losing the job."""

        next_status = str(status or "failed").strip().lower()
        if next_status not in {"pending", "failed"}:
            next_status = "failed"
        now = utcnow_iso()
        with self._lock, self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE nhp_memory
                    SET status = :status,
                        failure_reason = :failure_reason,
                        retry_count = retry_count + 1,
                        updated_at = :updated_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": str(memory_id),
                    "status": next_status,
                    "failure_reason": str(reason or ""),
                    "updated_at": now,
                },
            )
        job = self.get_nhp_memory_job(memory_id)
        if job is None:
            raise KeyError(f"Unknown durable NHP memory job: {memory_id}")
        return job

    def upsert_nhp_relationship(
        self,
        *,
        agent_identity: str,
        target_kind: str,
        target_identity: str,
        target_display_name: str = "",
        affinity_delta: int = 0,
        trust_delta: int = 0,
        friction_delta: int = 0,
        note: str = "",
        source_game_id: str = "",
    ) -> dict[str, Any]:
        """Apply one durable bounded relationship update for an NHP identity."""

        agent = str(agent_identity).strip()
        kind = str(target_kind).strip().lower()
        target = str(target_identity).strip()
        if not agent or kind not in {"nhp", "hp"} or not target:
            raise ValueError("Durable relationship updates require agent, target kind, and target identity.")
        relationship_id = _relationship_id(agent, kind, target)
        now = utcnow_iso()
        clean_note = str(note or "").strip()
        with self._lock, self.engine.begin() as conn:
            existing = self._first_or_none(
                conn.execute(text("SELECT * FROM nhp_relationships WHERE id = :id"), {"id": relationship_id}).mappings()
            )
            if existing is None:
                notes = [clean_note] if clean_note else []
                conn.execute(
                    text(
                        """
                        INSERT INTO nhp_relationships (
                            id, agent_identity, target_kind, target_identity, target_display_name,
                            affinity, trust, friction, notes_json, last_source_game_id, created_at, updated_at
                        )
                        VALUES (
                            :id, :agent_identity, :target_kind, :target_identity, :target_display_name,
                            :affinity, :trust, :friction, :notes_json, :last_source_game_id, :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "id": relationship_id,
                        "agent_identity": agent,
                        "target_kind": kind,
                        "target_identity": target,
                        "target_display_name": str(target_display_name or target),
                        "affinity": _clamp_int(affinity_delta, minimum=-5, maximum=5),
                        "trust": _clamp_int(trust_delta, minimum=-5, maximum=5),
                        "friction": _clamp_int(friction_delta, minimum=0, maximum=5),
                        "notes_json": json.dumps(notes[-8:]),
                        "last_source_game_id": str(source_game_id or ""),
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            else:
                notes = [str(item) for item in json.loads(existing["notes_json"] or "[]") if str(item).strip()]
                if clean_note:
                    notes.append(clean_note)
                conn.execute(
                    text(
                        """
                        UPDATE nhp_relationships
                        SET target_display_name = :target_display_name,
                            affinity = :affinity,
                            trust = :trust,
                            friction = :friction,
                            notes_json = :notes_json,
                            last_source_game_id = :last_source_game_id,
                            updated_at = :updated_at
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": relationship_id,
                        "target_display_name": str(target_display_name or existing["target_display_name"] or target),
                        "affinity": _clamp_int(int(existing["affinity"] or 0) + affinity_delta, minimum=-5, maximum=5),
                        "trust": _clamp_int(int(existing["trust"] or 0) + trust_delta, minimum=-5, maximum=5),
                        "friction": _clamp_int(int(existing["friction"] or 0) + friction_delta, minimum=0, maximum=5),
                        "notes_json": json.dumps(notes[-8:]),
                        "last_source_game_id": str(source_game_id or existing["last_source_game_id"] or ""),
                        "updated_at": now,
                    },
                )
            row = self._first_or_none(
                conn.execute(text("SELECT * FROM nhp_relationships WHERE id = :id"), {"id": relationship_id}).mappings()
            )
        if row is None:
            raise RuntimeError(f"Could not upsert durable NHP relationship: {relationship_id}")
        return self._relationship_row(row)

    def list_nhp_relationships(self, *, agent_identity: str = "", limit: int = 250) -> list[dict[str, Any]]:
        """Return durable NHP relationship rows for runtime context or admin inspection."""

        clauses: list[str] = []
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
        if str(agent_identity).strip():
            clauses.append("agent_identity = :agent_identity")
            params["agent_identity"] = str(agent_identity).strip()
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT *
                    FROM nhp_relationships
                    {where}
                    ORDER BY updated_at DESC, agent_identity, target_kind, target_identity
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings()
            return [self._relationship_row(dict(row)) for row in rows]

    def record_nhp_note(
        self,
        *,
        agent_identity: str,
        game_id: str,
        seat_id: str,
        note_kind: str,
        note_text: str = "",
        payload: dict[str, Any] | None = None,
        tool_name: str = "",
        target_kind: str = "",
        target_identity: str = "",
        target_display_name: str = "",
    ) -> dict[str, Any]:
        """Append one durable NHP memory/social/tool-audit note."""

        agent = str(agent_identity or "").strip()
        source_game_id = str(game_id or "").strip()
        source_seat_id = str(seat_id or "").strip()
        kind = str(note_kind or "tool_audit").strip().lower() or "tool_audit"
        if not agent or not source_game_id or not source_seat_id:
            raise ValueError("Durable NHP notes require agent identity, game id, and seat id.")
        now = utcnow_iso()
        row_id = _note_id()
        with self._lock, self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO nhp_notes (
                        id, note_kind, tool_name, agent_identity, source_game_id, source_seat_id,
                        target_kind, target_identity, target_display_name, note_text, payload_json, created_at
                    )
                    VALUES (
                        :id, :note_kind, :tool_name, :agent_identity, :source_game_id, :source_seat_id,
                        :target_kind, :target_identity, :target_display_name, :note_text, :payload_json, :created_at
                    )
                    """
                ),
                {
                    "id": row_id,
                    "note_kind": kind[:64],
                    "tool_name": str(tool_name or "")[:96],
                    "agent_identity": agent,
                    "source_game_id": source_game_id,
                    "source_seat_id": source_seat_id,
                    "target_kind": str(target_kind or "").strip().lower()[:16],
                    "target_identity": str(target_identity or "").strip()[:160],
                    "target_display_name": str(target_display_name or "").strip()[:160],
                    "note_text": str(note_text or "").strip()[:2000],
                    "payload_json": json.dumps(dict(payload or {})),
                    "created_at": now,
                },
            )
            row = self._first_or_none(
                conn.execute(text("SELECT * FROM nhp_notes WHERE id = :id"), {"id": row_id}).mappings()
            )
        if row is None:
            raise RuntimeError(f"Could not append durable NHP note: {row_id}")
        return self._note_row(row)

    def list_nhp_notes(
        self,
        *,
        agent_identity: str = "",
        game_id: str = "",
        seat_id: str = "",
        note_kind: str = "",
        target_kind: str = "",
        target_identity: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return durable NHP notes for runtime context and admin history views."""

        clauses: list[str] = []
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
        if str(agent_identity).strip():
            clauses.append("agent_identity = :agent_identity")
            params["agent_identity"] = str(agent_identity).strip()
        if str(game_id).strip():
            clauses.append("source_game_id = :game_id")
            params["game_id"] = str(game_id).strip()
        if str(seat_id).strip():
            clauses.append("source_seat_id = :seat_id")
            params["seat_id"] = str(seat_id).strip()
        if str(note_kind).strip():
            clauses.append("note_kind = :note_kind")
            params["note_kind"] = str(note_kind).strip().lower()
        if str(target_kind).strip():
            clauses.append("target_kind = :target_kind")
            params["target_kind"] = str(target_kind).strip().lower()
        if str(target_identity).strip():
            clauses.append("target_identity = :target_identity")
            params["target_identity"] = str(target_identity).strip()
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT *
                    FROM nhp_notes
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings()
            return [self._note_row(dict(row)) for row in rows]

    def recent_nhp_notes_for_agent(self, agent_identity: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """Return recent durable notes for one canonical NHP identity."""

        return self.list_nhp_notes(agent_identity=agent_identity, limit=limit)

    def list_nhp_history(self, *, agent_identity: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Return saved-game appearances for NHP history views."""

        clauses = ["s.seat_kind != 'human'"]
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
        if str(agent_identity).strip():
            clauses.append("s.character_name = :agent_identity")
            params["agent_identity"] = str(agent_identity).strip()
        where = "WHERE " + " AND ".join(clauses)
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT
                        s.game_id, g.title, g.status, s.seat_key, s.display_name,
                        s.character_name, s.seat_kind, s.agent_model, s.first_seen_at,
                        g.created_at AS game_created_at, g.updated_at AS game_updated_at
                    FROM seats AS s
                    JOIN games AS g ON g.id = s.game_id
                    {where}
                    ORDER BY g.updated_at DESC, g.created_at DESC, s.id
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings()
            return [dict(row) for row in rows]

    def list_human_player_history(self, *, player_identity: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Return saved-game appearances for human display-name history views."""

        normalized = normalize_player_identity(player_identity)
        clauses = ["s.seat_kind = 'human'"]
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT
                        s.game_id, g.title, g.status, s.seat_key, s.display_name,
                        s.character_name, s.first_seen_at,
                        g.created_at AS game_created_at, g.updated_at AS game_updated_at
                    FROM seats AS s
                    JOIN games AS g ON g.id = s.game_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY g.updated_at DESC, g.created_at DESC, s.id
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings()
            history = []
            for row in rows:
                item = dict(row)
                item["player_identity"] = normalize_player_identity(str(row["display_name"] or ""))
                if normalized and item["player_identity"] != normalized:
                    continue
                history.append(item)
            return history

    def admin_game_detail(self, game_id: str) -> dict[str, Any]:
        """Return a full admin-authorized game record with seats, events, and memory jobs."""

        record = self.get_game_record(game_id)
        if record is None:
            raise KeyError(f"Unknown game: {game_id}")
        with self.engine.begin() as conn:
            memory_rows = conn.execute(
                text(
                    """
                    SELECT *
                    FROM nhp_memory
                    WHERE source_game_id = :game_id
                    ORDER BY updated_at DESC, created_at DESC
                    """
                ),
                {"game_id": game_id},
            ).mappings()
            note_rows = conn.execute(
                text(
                    """
                    SELECT *
                    FROM nhp_notes
                    WHERE source_game_id = :game_id
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {"game_id": game_id},
            ).mappings()
        return {
            **record,
            "seats": self.list_seats(game_id),
            "events": self.events_for_game(game_id),
            "nhp_memory": [self._memory_row(dict(row)) for row in memory_rows],
            "nhp_notes": [self._note_row(dict(row)) for row in note_rows],
        }
