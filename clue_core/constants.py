"""Canonical Clue constants."""

from __future__ import annotations

SUSPECTS = (
    "Miss Scarlet",
    "Colonel Mustard",
    "Mrs. White",
    "Mr. Green",
    "Mrs. Peacock",
    "Professor Plum",
)

WEAPONS = (
    "Candlestick",
    "Knife",
    "Lead Pipe",
    "Revolver",
    "Rope",
    "Wrench",
)

ROOMS = (
    "Study",
    "Hall",
    "Lounge",
    "Library",
    "Billiard Room",
    "Dining Room",
    "Conservatory",
    "Ballroom",
    "Kitchen",
)

CHARACTERS = SUSPECTS

CARD_CATEGORIES = {
    "suspect": list(SUSPECTS),
    "weapon": list(WEAPONS),
    "room": list(ROOMS),
}


def card_category(card_name: str) -> str:
    """Map one card name to its case-file category."""

    if card_name in SUSPECTS:
        return "suspect"
    if card_name in WEAPONS:
        return "weapon"
    if card_name in ROOMS:
        return "room"
    raise KeyError(f"Unknown card: {card_name}")
