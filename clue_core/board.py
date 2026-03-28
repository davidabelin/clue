"""Abstract board graph for the classic Clue layout."""

from __future__ import annotations

from collections import deque


BOARD_NODES = {
    "study": {"id": "study", "label": "Study", "kind": "room", "x": 80, "y": 80},
    "hall": {"id": "hall", "label": "Hall", "kind": "room", "x": 330, "y": 80},
    "lounge": {"id": "lounge", "label": "Lounge", "kind": "room", "x": 580, "y": 80},
    "library": {"id": "library", "label": "Library", "kind": "room", "x": 80, "y": 270},
    "billiard": {"id": "billiard", "label": "Billiard Room", "kind": "room", "x": 330, "y": 270},
    "dining": {"id": "dining", "label": "Dining Room", "kind": "room", "x": 580, "y": 270},
    "conservatory": {"id": "conservatory", "label": "Conservatory", "kind": "room", "x": 80, "y": 470},
    "ballroom": {"id": "ballroom", "label": "Ballroom", "kind": "room", "x": 330, "y": 470},
    "kitchen": {"id": "kitchen", "label": "Kitchen", "kind": "room", "x": 580, "y": 470},
    "study_hall": {"id": "study_hall", "label": "Study / Hall", "kind": "hallway", "x": 205, "y": 80},
    "hall_lounge": {"id": "hall_lounge", "label": "Hall / Lounge", "kind": "hallway", "x": 455, "y": 80},
    "study_library": {"id": "study_library", "label": "Study / Library", "kind": "hallway", "x": 80, "y": 175},
    "hall_billiard": {"id": "hall_billiard", "label": "Hall / Billiard", "kind": "hallway", "x": 330, "y": 175},
    "lounge_dining": {"id": "lounge_dining", "label": "Lounge / Dining", "kind": "hallway", "x": 580, "y": 175},
    "library_billiard": {"id": "library_billiard", "label": "Library / Billiard", "kind": "hallway", "x": 205, "y": 270},
    "billiard_dining": {"id": "billiard_dining", "label": "Billiard / Dining", "kind": "hallway", "x": 455, "y": 270},
    "library_conservatory": {"id": "library_conservatory", "label": "Library / Conservatory", "kind": "hallway", "x": 80, "y": 370},
    "billiard_ballroom": {"id": "billiard_ballroom", "label": "Billiard / Ballroom", "kind": "hallway", "x": 330, "y": 370},
    "dining_kitchen": {"id": "dining_kitchen", "label": "Dining / Kitchen", "kind": "hallway", "x": 580, "y": 370},
    "conservatory_ballroom": {
        "id": "conservatory_ballroom",
        "label": "Conservatory / Ballroom",
        "kind": "hallway",
        "x": 205,
        "y": 470,
    },
    "ballroom_kitchen": {"id": "ballroom_kitchen", "label": "Ballroom / Kitchen", "kind": "hallway", "x": 455, "y": 470},
    "scarlet_start": {"id": "scarlet_start", "label": "Scarlet Start", "kind": "start", "x": 690, "y": 120},
    "mustard_start": {"id": "mustard_start", "label": "Mustard Start", "kind": "start", "x": 690, "y": 250},
    "white_start": {"id": "white_start", "label": "White Start", "kind": "start", "x": 690, "y": 380},
    "green_start": {"id": "green_start", "label": "Green Start", "kind": "start", "x": 690, "y": 500},
    "peacock_start": {"id": "peacock_start", "label": "Peacock Start", "kind": "start", "x": 20, "y": 500},
    "plum_start": {"id": "plum_start", "label": "Plum Start", "kind": "start", "x": 20, "y": 120},
}

GRAPH = {
    "study": ("study_hall", "study_library"),
    "hall": ("study_hall", "hall_lounge", "hall_billiard"),
    "lounge": ("hall_lounge", "lounge_dining"),
    "library": ("study_library", "library_billiard", "library_conservatory"),
    "billiard": ("hall_billiard", "library_billiard", "billiard_dining", "billiard_ballroom"),
    "dining": ("lounge_dining", "billiard_dining", "dining_kitchen"),
    "conservatory": ("library_conservatory", "conservatory_ballroom"),
    "ballroom": ("conservatory_ballroom", "ballroom_kitchen", "billiard_ballroom"),
    "kitchen": ("dining_kitchen", "ballroom_kitchen"),
    "study_hall": ("study", "hall"),
    "hall_lounge": ("hall", "lounge", "scarlet_start"),
    "study_library": ("study", "library", "plum_start"),
    "hall_billiard": ("hall", "billiard"),
    "lounge_dining": ("lounge", "dining", "mustard_start"),
    "library_billiard": ("library", "billiard"),
    "billiard_dining": ("billiard", "dining"),
    "library_conservatory": ("library", "conservatory", "peacock_start"),
    "billiard_ballroom": ("billiard", "ballroom"),
    "dining_kitchen": ("dining", "kitchen"),
    "conservatory_ballroom": ("conservatory", "ballroom", "green_start"),
    "ballroom_kitchen": ("ballroom", "kitchen", "white_start"),
    "scarlet_start": ("hall_lounge",),
    "mustard_start": ("lounge_dining",),
    "white_start": ("ballroom_kitchen",),
    "green_start": ("conservatory_ballroom",),
    "peacock_start": ("library_conservatory",),
    "plum_start": ("study_library",),
}

SECRET_PASSAGES = {
    "study": "kitchen",
    "kitchen": "study",
    "lounge": "conservatory",
    "conservatory": "lounge",
}

CHARACTER_START_NODES = {
    "Miss Scarlet": "scarlet_start",
    "Colonel Mustard": "mustard_start",
    "Mrs. White": "white_start",
    "Mr. Green": "green_start",
    "Mrs. Peacock": "peacock_start",
    "Professor Plum": "plum_start",
}

ROOM_NAME_TO_NODE = {
    "Study": "study",
    "Hall": "hall",
    "Lounge": "lounge",
    "Library": "library",
    "Billiard Room": "billiard",
    "Dining Room": "dining",
    "Conservatory": "conservatory",
    "Ballroom": "ballroom",
    "Kitchen": "kitchen",
}
NODE_TO_ROOM_NAME = {node_id: label for label, node_id in ROOM_NAME_TO_NODE.items()}


def node_kind(node_id: str) -> str:
    """Return the board-node kind for one node id."""

    return str(BOARD_NODES[node_id]["kind"])


def is_room(node_id: str) -> bool:
    """Report whether one node id is a room rather than a hallway or start node."""

    return node_kind(node_id) == "room"


def shortest_paths(start: str, blocked: set[str] | None = None) -> dict[str, int]:
    """Compute shortest graph distances while treating occupied hallways as blocked."""

    blocked_nodes = {item for item in (blocked or set()) if node_kind(item) == "hallway"}
    distances = {start: 0}
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in GRAPH[current]:
            if neighbor in blocked_nodes and neighbor != start:
                continue
            if neighbor in distances:
                continue
            distances[neighbor] = distances[current] + 1
            queue.append(neighbor)
    return distances


def reachable_nodes(start: str, steps: int, blocked: set[str] | None = None) -> dict[str, int]:
    """Return nodes reachable within the rolled movement budget."""

    distances = shortest_paths(start, blocked=blocked)
    return {
        node_id: distance
        for node_id, distance in distances.items()
        if 0 < distance <= int(steps)
    }
