"""Repository tests for durable NHP memory and relationship persistence."""

from __future__ import annotations

from pathlib import Path

from clue_storage.repository import ClueRepository, normalize_player_identity


def _repository(tmp_path: Path) -> ClueRepository:
    """Create one isolated repository with the durable-memory schema."""

    repository = ClueRepository(str(tmp_path / "clue.db"))
    repository.init_schema()
    return repository


def _create_saved_game(repository: ClueRepository) -> None:
    """Persist a compact game row sufficient for repository-level tests."""

    state = {
        "game_id": "game_repo",
        "title": "Repo Table",
        "status": "complete",
        "seats": {},
    }
    repository.create_game(
        game_id="game_repo",
        title="Repo Table",
        config={"game_id": "game_repo"},
        setup={"seed": 1},
        state=state,
        seats=[
            {
                "seat_id": "seat_scarlet",
                "display_name": "Miss Scarlet",
                "character": "Miss Scarlet",
                "seat_kind": "llm",
                "agent_model": "",
                "notebook": {},
            }
        ],
        seat_tokens=[],
        events=[],
    )


def test_memory_job_lifecycle_is_idempotent(tmp_path: Path):
    """NHP memory jobs should be unique per source game and seat."""

    repository = _repository(tmp_path)
    _create_saved_game(repository)

    first = repository.ensure_nhp_memory_job(
        game_id="game_repo",
        seat_id="seat_scarlet",
        character="Miss Scarlet",
        display_name="Miss Scarlet",
    )
    second = repository.ensure_nhp_memory_job(
        game_id="game_repo",
        seat_id="seat_scarlet",
        character="Miss Scarlet",
        display_name="Miss Scarlet",
    )
    pending = repository.mark_nhp_memory_failure(first["id"], reason="missing_api_key", status="pending")
    ready = repository.mark_nhp_memory_ready(
        first["id"],
        summary={"first_person_summary": "I will remember that table."},
        model_meta={"model": "test-model"},
    )

    assert first["id"] == second["id"]
    assert pending["retry_count"] == 1
    assert pending["status"] == "pending"
    assert ready["status"] == "ready"
    assert ready["summary"]["first_person_summary"] == "I will remember that table."
    assert repository.ready_nhp_memory_for_agent("Miss Scarlet")[0]["id"] == first["id"]


def test_relationship_upsert_clamps_and_retains_notes(tmp_path: Path):
    """Durable relationship updates should accumulate inside bounded scores."""

    repository = _repository(tmp_path)
    first = repository.upsert_nhp_relationship(
        agent_identity="Miss Scarlet",
        target_kind="hp",
        target_identity=normalize_player_identity("  Dr.  Orchid "),
        target_display_name="Dr. Orchid",
        affinity_delta=9,
        trust_delta=-9,
        friction_delta=2,
        note="Kept a promise.",
        source_game_id="game_repo",
    )
    second = repository.upsert_nhp_relationship(
        agent_identity="Miss Scarlet",
        target_kind="hp",
        target_identity=normalize_player_identity("Dr. Orchid"),
        target_display_name="Dr. Orchid",
        affinity_delta=-2,
        trust_delta=1,
        friction_delta=4,
        note="Pressed too hard later.",
        source_game_id="game_repo_2",
    )

    assert first["affinity"] == 5
    assert first["trust"] == -5
    assert second["affinity"] == 3
    assert second["trust"] == -4
    assert second["friction"] == 5
    assert second["notes"] == ["Kept a promise.", "Pressed too hard later."]


def test_admin_game_listing_and_detail_include_memory(tmp_path: Path):
    """Admin repository helpers should expose saved games and related memory rows."""

    repository = _repository(tmp_path)
    _create_saved_game(repository)
    job = repository.ensure_nhp_memory_job(
        game_id="game_repo",
        seat_id="seat_scarlet",
        character="Miss Scarlet",
        display_name="Miss Scarlet",
    )

    games = repository.list_games()
    detail = repository.admin_game_detail("game_repo")

    assert games[0]["id"] == "game_repo"
    assert detail["id"] == "game_repo"
    assert detail["nhp_memory"][0]["id"] == job["id"]
