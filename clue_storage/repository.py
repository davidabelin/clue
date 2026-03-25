"""Repository layer for Clue game state, seats, tokens, and event history."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, MappingResult

from clue_core.events import utcnow_iso


def _looks_like_database_url(value: str) -> bool:
    return "://" in str(value)


def _to_sqlite_url(path_value: str) -> str:
    path = Path(path_value).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{path.as_posix()}"


class ClueRepository:
    """Persistence facade for standalone and AIX-mounted Clue gameplay."""

    def __init__(self, db_target: str) -> None:
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
        row = rows.first()
        return dict(row) if row is not None else None

    def _run_script(self, script: str) -> None:
        statements = [part.strip() for part in script.split(";") if part.strip()]
        with self.engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))

    def init_schema(self) -> None:
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

            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_game_index ON events(game_id, event_index);
            CREATE INDEX IF NOT EXISTS idx_seats_game ON seats(game_id);
            CREATE INDEX IF NOT EXISTS idx_events_game_id ON events(game_id, id);
            CREATE INDEX IF NOT EXISTS idx_tokens_game ON seat_tokens(game_id);
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

            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_game_index ON events(game_id, event_index);
            CREATE INDEX IF NOT EXISTS idx_seats_game ON seats(game_id);
            CREATE INDEX IF NOT EXISTS idx_events_game_id ON events(game_id, id);
            CREATE INDEX IF NOT EXISTS idx_tokens_game ON seat_tokens(game_id);
        """
        self._run_script(sqlite_schema if self._dialect == "sqlite" else postgres_schema)

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
            for seat in seats:
                conn.execute(
                    text(
                        """
                        INSERT INTO seats (
                            id, game_id, display_name, character_name, seat_kind, agent_model,
                            notebook_json, first_seen_at, created_at, updated_at
                        )
                        VALUES (
                            :id, :game_id, :display_name, :character_name, :seat_kind, :agent_model,
                            :notebook_json, NULL, :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "id": seat["seat_id"],
                        "game_id": game_id,
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
                        "seat_id": token_row["seat_id"],
                        "issued_at": created_at,
                    },
                )
            self._insert_events(conn, game_id=game_id, events=events)

    def _insert_events(self, conn, *, game_id: str, events: list[dict[str, Any]]) -> None:
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
        row = self.get_game_record(game_id)
        if row is None:
            raise KeyError(f"Unknown game: {game_id}")
        return dict(row["state"])

    def save_state_and_events(self, game_id: str, *, state: dict[str, Any], events: list[dict[str, Any]]) -> None:
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
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT * FROM seats WHERE game_id = :game_id ORDER BY created_at, id"),
                {"game_id": game_id},
            ).mappings()
            return [
                {
                    "seat_id": row["id"],
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
        with self.engine.begin() as conn:
            row = self._first_or_none(
                conn.execute(
                    text(
                        """
                        SELECT t.token, t.game_id, t.seat_id, s.display_name, s.character_name, s.seat_kind,
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
            "seat_id": row["seat_id"],
            "display_name": row["display_name"],
            "character": row["character_name"],
            "seat_kind": row["seat_kind"],
            "agent_model": row["agent_model"],
            "notebook": json.loads(row["notebook_json"] or "{}"),
            "first_seen_at": row["first_seen_at"],
        }

    def mark_seat_seen(self, seat_id: str) -> None:
        with self._lock, self.engine.begin() as conn:
            now = utcnow_iso()
            conn.execute(
                text(
                    """
                    UPDATE seats
                    SET first_seen_at = COALESCE(first_seen_at, :now), updated_at = :now
                    WHERE id = :seat_id
                    """
                ),
                {"seat_id": seat_id, "now": now},
            )

    def update_notebook(self, seat_id: str, notebook: dict[str, Any]) -> None:
        with self._lock, self.engine.begin() as conn:
            conn.execute(
                text("UPDATE seats SET notebook_json = :notebook_json, updated_at = :updated_at WHERE id = :seat_id"),
                {
                    "seat_id": seat_id,
                    "notebook_json": json.dumps(notebook),
                    "updated_at": utcnow_iso(),
                },
            )

    def visible_events(self, game_id: str, *, seat_id: str, since_event_index: int = 0) -> list[dict[str, Any]]:
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
