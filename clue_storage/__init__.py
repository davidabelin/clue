"""Persistence facade for the Clue lab."""

from clue_storage.repository import ClueRepository, normalize_player_identity

__all__ = ["ClueRepository", "normalize_player_identity"]
