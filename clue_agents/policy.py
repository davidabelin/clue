"""Shared autonomous-seat helpers for accusation pacing and stock table talk."""

from __future__ import annotations

from typing import Any

from clue_agents.profile_loader import build_persona_guidance, build_social_guidance, persona_chat_examples
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

IDLE_CHAT_LINES = {
    "Miss Scarlet": {
        "reply": ("If you're calling on me, {speaker}, at least make it entertaining.",),
        "chat": ("Keep talking, {speaker}. Someone will slip.",),
        "narrative": ("That little flourish from {actor} told me plenty.",),
        "generic": ("This table is trying far too hard to look innocent.",),
    },
    "Colonel Mustard": {
        "reply": ("Make your point plainly, {speaker}.",),
        "chat": ("Noted, {speaker}. Keep it sharp.",),
        "narrative": ("{actor} just changed the field a bit.",),
        "generic": ("Enough posing. The room is giving something away.",),
    },
    "Mrs. White": {
        "reply": ("If you mean to involve me, {speaker}, do be tidy about it.",),
        "chat": ("How revealing, {speaker}.",),
        "narrative": ("{actor} managed to stir up quite a bit with that.",),
        "generic": ("A little less noise would help, though the noise is useful.",),
    },
    "Mr. Green": {
        "reply": ("If you're asking me, {speaker}, be direct.",),
        "chat": ("Fair enough, {speaker}. That gives us something to work with.",),
        "narrative": ("{actor} just gave the table something concrete to think about.",),
        "generic": ("The reactions matter more than the posture right now.",),
    },
    "Mrs. Peacock": {
        "reply": ("If we are invoking me, {speaker}, do try to be graceful about it.",),
        "chat": ("Please continue, {speaker}. The table is practically gossiping for us.",),
        "narrative": ("{actor} has made things ever so much more interesting.",),
        "generic": ("How charming. Everyone is pretending not to flinch.",),
    },
    "Professor Plum": {
        "reply": ("If you require my attention, {speaker}, do try to be precise.",),
        "chat": ("Interesting, {speaker}. The pattern is becoming less subtle.",),
        "narrative": ("{actor} just added a useful wrinkle to the evidence.",),
        "generic": ("The social theater is noisy, but the information leakage is excellent.",),
    },
}


def persona_prompt(character: str) -> str:
    """Return the public-voice guidance for one stock character."""

    persona = CHARACTER_PERSONAS.get(character) or {}
    yaml_guidance = build_persona_guidance(character)
    fallback = str(persona.get("prompt") or "Keep public chat brief, safe, and lightly in character.")
    if yaml_guidance:
        return f"{yaml_guidance}\nFallback voice cue: {fallback}"
    return fallback


def social_prompt(character: str) -> str:
    """Return the chat-only persona guidance block for one stock character."""

    persona = CHARACTER_PERSONAS.get(character) or {}
    yaml_guidance = build_social_guidance(character)
    fallback = str(persona.get("prompt") or "Stay in character, concise, and socially reactive.")
    if yaml_guidance:
        return f"{yaml_guidance}\nFallback voice cue: {fallback}"
    return fallback


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
        line = _choose_template(persona["suggest"], turn_index).format(
            suspect=suspect,
            weapon=weapon,
            room=room_name,
        )
        return _with_social_action_lead(snapshot, line)
    if action == "accuse":
        suspect = str(decision.get("suspect") or "someone")
        weapon = str(decision.get("weapon") or "something")
        room_name = str(decision.get("room") or "somewhere")
        line = _choose_template(persona["accuse"], turn_index).format(
            suspect=suspect,
            weapon=weapon,
            room=room_name,
        )
        return _with_social_action_lead(snapshot, line)
    if action == "move" and _is_secret_passage_move(snapshot, decision):
        target_node = str(decision.get("target_node") or "")
        target_label = str(BOARD_NODES.get(target_node, {}).get("label") or NODE_TO_ROOM_NAME.get(target_node) or "the next room")
        return _choose_template(persona["passage"], turn_index).format(target=target_label)
    return ""


def player_facing_public_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the public event stream visible to players, excluding trace rows."""

    return [
        dict(event)
        for event in snapshot.get("events") or []
        if str(event.get("visibility") or "") == "public" and not str(event.get("event_type") or "").startswith("trace_")
    ]


def public_chat_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the visible public chat events for the current snapshot."""

    return [event for event in player_facing_public_events(snapshot) if str(event.get("event_type") or "") == "chat_posted"]


def public_narrative_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the visible non-chat public narrative events for the current snapshot."""

    return [event for event in player_facing_public_events(snapshot) if str(event.get("event_type") or "") != "chat_posted"]


def event_actor_seat_id(event: dict[str, Any]) -> str:
    """Best-effort extraction of the acting seat id from one event payload."""

    payload = dict(event.get("payload") or {})
    for key in ("seat_id", "from_seat_id", "winner_seat_id", "suggester"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def stock_idle_chat(snapshot: dict[str, Any]) -> str:
    """Compose one short in-character idle table-talk line from public context."""

    seat = dict(snapshot.get("seat") or {})
    character = str(seat.get("character") or "")
    examples = persona_chat_examples(character)
    seat_social = current_seat_social_state(snapshot)
    active_thread = current_social_thread(snapshot)
    persona = IDLE_CHAT_LINES.get(character)
    if not persona:
        return ""

    turn_index = int(snapshot.get("turn_index") or 0)
    public_events = player_facing_public_events(snapshot)
    if not public_events:
        return _choose_template(persona["generic"], turn_index)

    latest_event = public_events[-1]
    chats = public_chat_events(snapshot)
    narratives = public_narrative_events(snapshot)
    latest_chat = chats[-1] if chats else {}
    latest_narrative = narratives[-1] if narratives else {}
    speaker_name = _seat_display_name(snapshot, event_actor_seat_id(latest_chat)) or "someone"
    actor_name = _seat_display_name(snapshot, event_actor_seat_id(latest_narrative)) or "someone"
    target_name = _seat_display_name(snapshot, str(seat_social.get("focus_seat_id") or "")) or speaker_name

    if active_thread:
        intent_name = {
            "dispute": "reconcile" if str(active_thread.get("status") or "") == "cooling" else "challenge",
            "alliance": "ally",
            "flirtation": "tease",
            "meta": "meta_observe",
            "banter": "tease",
        }.get(str(active_thread.get("kind") or ""), "meta_observe")
        line = _persona_chat_example_line(
            examples,
            intent_name,
            speaker=speaker_name,
            actor=actor_name,
            target=target_name,
            topic=str(active_thread.get("topic") or ""),
        )
        if line:
            return line

    if latest_chat and _event_mentions_current_seat(latest_chat, snapshot):
        line = _persona_chat_example_line(examples, "deflect", speaker=speaker_name, actor=actor_name, target=target_name, topic="")
        if line:
            return line
        return _choose_template(persona["reply"], turn_index).format(speaker=speaker_name)
    if str(latest_event.get("event_type") or "") == "chat_posted" and speaker_name != (seat.get("display_name") or ""):
        line = _persona_chat_example_line(examples, "tease", speaker=speaker_name, actor=actor_name, target=target_name, topic="")
        if line:
            return line
        return _choose_template(persona["chat"], turn_index).format(speaker=speaker_name)
    if latest_narrative:
        line = _persona_chat_example_line(examples, "meta_observe", speaker=speaker_name, actor=actor_name, target=target_name, topic="")
        if line:
            return line
        return _choose_template(persona["narrative"], turn_index).format(actor=actor_name)
    line = _persona_chat_example_line(examples, "meta_observe", speaker=speaker_name, actor=actor_name, target=target_name, topic="")
    if line:
        return line
    return _choose_template(persona["generic"], turn_index)


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


def _event_mentions_current_seat(event: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    """Report whether one public chat event names the current seat in plain text."""

    seat = dict(snapshot.get("seat") or {})
    text = str((event.get("payload") or {}).get("text") or event.get("message") or "").lower()
    display_name = str(seat.get("display_name") or "").lower()
    character = str(seat.get("character") or "").lower()
    return bool(text and ((display_name and display_name in text) or (character and character in text)))


def _seat_display_name(snapshot: dict[str, Any], seat_id: str) -> str:
    """Resolve one seat id to its display name in the current snapshot."""

    if not seat_id:
        return ""
    current = dict(snapshot.get("seat") or {})
    if str(current.get("seat_id") or "") == seat_id:
        return str(current.get("display_name") or "")
    for seat in snapshot.get("seats") or []:
        if str(seat.get("seat_id") or "") == seat_id:
            return str(seat.get("display_name") or "")
    return ""


def current_seat_social_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the current seat's filtered social-memory state."""

    return dict((snapshot.get("social") or {}).get("seat_state") or {})


def current_social_thread(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the hottest active thread currently involving the seat."""

    return dict((snapshot.get("social") or {}).get("hottest_thread") or {})


def _with_social_action_lead(snapshot: dict[str, Any], line: str) -> str:
    """Prefix action comments with a small thread-aware cue when useful."""

    if not line:
        return line
    thread = current_social_thread(snapshot)
    kind = str(thread.get("kind") or "")
    if kind == "dispute":
        return f"Since we're being blunt, {line[0].lower() + line[1:]}" if len(line) > 1 else line
    if kind == "alliance":
        return f"For once, let's be practical. {line}"
    if kind == "meta":
        return f"Setting the chatter aside, {line[0].lower() + line[1:]}" if len(line) > 1 else line
    return line


def _persona_chat_example_line(
    examples: dict[str, list[str]],
    intent_name: str,
    *,
    speaker: str,
    actor: str,
    target: str,
    topic: str,
) -> str:
    """Render the first matching YAML chat example with safe placeholder formatting."""

    choices = list(examples.get(intent_name) or [])
    if not choices:
        return ""
    template = str(choices[0]).strip()
    if not template:
        return ""
    try:
        return template.format(speaker=speaker, actor=actor, target=target, topic=topic)
    except Exception:
        return template


def _is_secret_passage_move(snapshot: dict[str, Any], decision: dict[str, Any]) -> bool:
    """Report whether one move decision is the optional zero-cost passage move."""

    if str(decision.get("action") or "") != "move":
        return False
    target_node = str(decision.get("target_node") or "")
    for option in snapshot.get("legal_actions", {}).get("move_targets") or []:
        if str(option.get("node_id") or "") == target_node and str(option.get("mode") or "") == "passage":
            return True
    return False
