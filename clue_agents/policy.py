"""Shared autonomous-seat helpers for accusation pacing and stock table talk."""

from __future__ import annotations

from typing import Any

from clue_core.board import BOARD_NODES, NODE_TO_ROOM_NAME


CHARACTER_PERSONAS = {
    "Miss Scarlet": {
        "prompt": "Quick, theatrical, and a little ruthless. Keep public chat short, stylish, and pointed.",
        "suggest": (
            "Let's bring {suspect}, the {weapon}, and the {room} into the brightest possible light.",
            "Pressure first. {suspect}, with the {weapon}, in the {room}.",
            "If anyone is bluffing, this should flush it out: {suspect}, {weapon}, {room}.",
        ),
        "accuse": (
            "Enough circling. I am naming {suspect}, with the {weapon}, in the {room}.",
            "The curtain is up: {suspect}, {weapon}, {room}.",
        ),
        "passage": (
            "A side route will do nicely. I am slipping toward the {target}.",
        ),
    },
    "Colonel Mustard": {
        "prompt": "Direct, decisive, and clipped. Sound like an old officer stating the obvious next step.",
        "suggest": (
            "Straight to it: {suspect}, the {weapon}, in the {room}.",
            "I want a clean answer on {suspect} with the {weapon} in the {room}.",
            "No dithering. Test {suspect}, {weapon}, {room}.",
        ),
        "accuse": (
            "I have heard enough. {suspect}, with the {weapon}, in the {room}.",
            "Final call: {suspect}, {weapon}, {room}.",
        ),
        "passage": (
            "Changing the angle. I am taking the passage to the {target}.",
        ),
    },
    "Mrs. White": {
        "prompt": "Measured, tidy, and dryly skeptical. Keep the line neat and understated.",
        "suggest": (
            "Let's tidy this question up: {suspect}, the {weapon}, in the {room}.",
            "One careful test, then: {suspect}, {weapon}, {room}.",
            "I would like a proper answer about {suspect} and the {weapon} in the {room}.",
        ),
        "accuse": (
            "The clutter is gone. It was {suspect}, with the {weapon}, in the {room}.",
            "Time to put this away: {suspect}, {weapon}, {room}.",
        ),
        "passage": (
            "A quieter route suits me. I am moving through to the {target}.",
        ),
    },
    "Mr. Green": {
        "prompt": "Practical, even-tempered, and quietly confident. Keep the line grounded and useful.",
        "suggest": (
            "Let's follow the practical lead: {suspect}, the {weapon}, in the {room}.",
            "Best next check is {suspect} with the {weapon} in the {room}.",
            "We can narrow this quickly with {suspect}, {weapon}, {room}.",
        ),
        "accuse": (
            "The practical answer is {suspect}, with the {weapon}, in the {room}.",
            "This is where the evidence lands: {suspect}, {weapon}, {room}.",
        ),
        "passage": (
            "Better route available. I am heading for the {target}.",
        ),
    },
    "Mrs. Peacock": {
        "prompt": "Elegant, cutting, and socially polished. Keep the line graceful but unmistakably strategic.",
        "suggest": (
            "If we are being civilized, we can still test {suspect} with the {weapon} in the {room}.",
            "A refined little question: {suspect}, the {weapon}, in the {room}.",
            "Let us see who flinches at {suspect}, {weapon}, {room}.",
        ),
        "accuse": (
            "Very well. It was {suspect}, with the {weapon}, in the {room}.",
            "No more polite delay: {suspect}, {weapon}, {room}.",
        ),
        "passage": (
            "A discreet change of scene. I am taking the passage to the {target}.",
        ),
    },
    "Professor Plum": {
        "prompt": "Analytical, professorly, and faintly smug. Keep the line clever but brief.",
        "suggest": (
            "A working theory, then: {suspect}, the {weapon}, in the {room}.",
            "The next clean inference comes from {suspect}, {weapon}, {room}.",
            "For the sake of rigor, test {suspect} with the {weapon} in the {room}.",
        ),
        "accuse": (
            "The deduction is complete: {suspect}, with the {weapon}, in the {room}.",
            "My conclusion is ready: {suspect}, {weapon}, {room}.",
        ),
        "passage": (
            "A lateral move seems indicated. I am taking the passage to the {target}.",
        ),
    },
}


def persona_prompt(character: str) -> str:
    """Return the public-voice guidance for one stock character."""

    persona = CHARACTER_PERSONAS.get(character) or {}
    return str(persona.get("prompt") or "Keep public chat brief, safe, and lightly in character.")


def accusation_window(snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Decide whether an autonomous seat has enough public/private evidence to accuse."""

    accusation = dict(tool_snapshot.get("accusation") or {})
    belief_summary = dict(tool_snapshot.get("belief_summary") or {})
    confidence = float(accusation.get("confidence") or 0.0)
    confidence_gap = float(accusation.get("confidence_gap") or 0.0)
    entropy_bits = float(accusation.get("entropy_bits") or belief_summary.get("joint_case_entropy_bits") or 99.0)
    sample_count = int(accusation.get("sample_count") or tool_snapshot.get("sample_count") or 0)
    turn_index = int(snapshot.get("turn_index") or 0)
    evidence_score = _visible_evidence_score(snapshot, belief_summary)
    hold_reasons: list[str] = []

    if not accusation.get("should_accuse"):
        hold_reasons.append("base threshold not reached")
    lock_case = confidence >= 0.985 and (confidence_gap >= 0.45 or entropy_bits <= 0.18)
    if not lock_case:
        if confidence < 0.82:
            hold_reasons.append("confidence still shallow")
        if confidence_gap < 0.18:
            hold_reasons.append("runner-up case still too close")
        if entropy_bits > 1.05:
            hold_reasons.append("case entropy still too high")
        if sample_count < 18:
            hold_reasons.append("belief sample too small")
        if turn_index < 4:
            hold_reasons.append("too early in the table cycle")
        if evidence_score < 2 and not (turn_index >= 8 and confidence >= 0.9 and confidence_gap >= 0.22):
            hold_reasons.append("not enough confirming evidence yet")

    return {
        "ready": not hold_reasons,
        "lock_case": lock_case,
        "confidence": round(confidence, 4),
        "confidence_gap": round(confidence_gap, 4),
        "entropy_bits": round(entropy_bits, 4) if entropy_bits < 99 else 99.0,
        "sample_count": sample_count,
        "turn_index": turn_index,
        "evidence_score": evidence_score,
        "hold_reasons": hold_reasons,
    }


def stock_public_comment(snapshot: dict[str, Any], decision: dict[str, Any]) -> str:
    """Compose one short public-safe stock comment for a meaningful autonomous action."""

    seat = dict(snapshot.get("seat") or {})
    character = str(seat.get("character") or "")
    persona = CHARACTER_PERSONAS.get(character)
    if not persona:
        return ""

    action = str(decision.get("action") or "")
    turn_index = int(snapshot.get("turn_index") or 0)
    if action == "suggest":
        room_name = str(snapshot.get("legal_actions", {}).get("current_room") or decision.get("room") or "this room")
        suspect = str(decision.get("suspect") or "someone")
        weapon = str(decision.get("weapon") or "something sharp")
        return _choose_template(persona["suggest"], turn_index).format(
            suspect=suspect,
            weapon=weapon,
            room=room_name,
        )
    if action == "accuse":
        suspect = str(decision.get("suspect") or "someone")
        weapon = str(decision.get("weapon") or "something")
        room_name = str(decision.get("room") or "somewhere")
        return _choose_template(persona["accuse"], turn_index).format(
            suspect=suspect,
            weapon=weapon,
            room=room_name,
        )
    if action == "move" and _is_secret_passage_move(snapshot, decision):
        target_node = str(decision.get("target_node") or "")
        target_label = str(BOARD_NODES.get(target_node, {}).get("label") or NODE_TO_ROOM_NAME.get(target_node) or "the next room")
        return _choose_template(persona["passage"], turn_index).format(target=target_label)
    return ""


def _visible_evidence_score(snapshot: dict[str, Any], belief_summary: dict[str, Any]) -> int:
    """Estimate how much concrete evidence the current seat has actually seen."""

    seat = dict(snapshot.get("seat") or {})
    seat_id = str(seat.get("seat_id") or "")
    events = list(snapshot.get("events") or [])
    private_shows = sum(
        1
        for event in events
        if str(event.get("event_type")) == "private_card_shown"
        and str((event.get("payload") or {}).get("to_seat_id", "")) == seat_id
    )
    self_unanswered = sum(
        1
        for event in events
        if str(event.get("event_type")) == "suggestion_unanswered"
        and str((event.get("payload") or {}).get("seat_id", "")) == seat_id
    )
    public_wrong_accusations = sum(1 for event in events if str(event.get("event_type")) == "accusation_wrong")
    candidate_counts = {
        str(category): int(count)
        for category, count in dict(belief_summary.get("case_file_candidate_counts") or {}).items()
    }
    tight_case = bool(candidate_counts) and all(count <= 2 for count in candidate_counts.values())
    resolved_cards = int(belief_summary.get("resolved_cards") or 0)
    hand_size = len(list(seat.get("hand") or []))

    score = 0
    if private_shows:
        score += 1
    if self_unanswered:
        score += 1
    if public_wrong_accusations:
        score += 1
    if tight_case:
        score += 1
    if resolved_cards >= hand_size + 4:
        score += 1
    return score


def _choose_template(templates: tuple[str, ...], turn_index: int) -> str:
    """Pick one deterministic stock line so repeated games vary slightly by turn."""

    if not templates:
        return ""
    return templates[turn_index % len(templates)]


def _is_secret_passage_move(snapshot: dict[str, Any], decision: dict[str, Any]) -> bool:
    """Report whether one move decision is the optional zero-cost passage move."""

    if str(decision.get("action") or "") != "move":
        return False
    target_node = str(decision.get("target_node") or "")
    for option in snapshot.get("legal_actions", {}).get("move_targets") or []:
        if str(option.get("node_id") or "") == target_node and str(option.get("mode") or "") == "passage":
            return True
    return False
