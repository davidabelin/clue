"""Core rules, board, events, and inference helpers for Clue."""

from clue_core.board import BOARD_NODES, CHARACTER_START_NODES, SECRET_PASSAGES
from clue_core.constants import CHARACTERS, ROOMS, SUSPECTS, WEAPONS
from clue_core.types import SeatConfig
from clue_core.version import CLUE_RELEASE_LABEL, CLUE_VERSION

__all__ = [
    "BOARD_NODES",
    "CHARACTERS",
    "CHARACTER_START_NODES",
    "CLUE_RELEASE_LABEL",
    "CLUE_VERSION",
    "ROOMS",
    "SECRET_PASSAGES",
    "SUSPECTS",
    "SeatConfig",
    "WEAPONS",
]
