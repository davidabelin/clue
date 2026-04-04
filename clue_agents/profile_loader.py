"""Load maintainer-authored persona and model-profile YAML for Clue seat agents.

This module is intentionally narrow:
- read and cache the YAML files under ``clue_agents/profiles/``
- turn persona settings into short prompt guidance for LLM seats
- deterministically assign one LLM runtime profile to each nonhuman seat at game creation

If a file is missing or malformed, callers fall back to the existing hardcoded
runtime behavior rather than failing closed during gameplay.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
from pathlib import Path
import random
from typing import Any

import yaml


PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
PERSONAS_PATH = PROFILE_DIR / "personas.yaml"
MODELS_PATH = PROFILE_DIR / "models.yaml"


@dataclass(slots=True)
class ModelProfileSelection:
    """One selected LLM runtime profile for a seat.

    The selection keeps both the profile id and the resolved payload so callers
    can surface stable diagnostics while still applying the concrete runtime
    values chosen from ``models.yaml``.
    """

    profile_id: str
    profile: dict[str, Any]

    @property
    def model(self) -> str:
        """Return the selected model snapshot name."""

        return str(self.profile.get("model") or "")

    @property
    def public_label(self) -> str:
        """Return the maintainer-facing display label for this profile."""

        return str(self.profile.get("public_label") or self.profile_id)


def clear_profile_caches() -> None:
    """Clear the cached YAML payloads for tests or hot-reload-ish workflows."""

    load_persona_catalog.cache_clear()
    load_model_catalog.cache_clear()


def _safe_yaml_map(path: Path) -> dict[str, Any]:
    """Read one YAML mapping file, returning an empty dict on any failure."""

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


@lru_cache(maxsize=1)
def load_persona_catalog() -> dict[str, Any]:
    """Load the persona catalog from disk once per process.

    Persona loading stays cached because profile selection and prompt assembly
    can happen repeatedly during one request cycle or test run.
    """

    return _safe_yaml_map(PERSONAS_PATH)


@lru_cache(maxsize=1)
def load_model_catalog() -> dict[str, Any]:
    """Load the model-profile catalog from disk once per process."""

    return _safe_yaml_map(MODELS_PATH)


def persona_profile(character: str) -> dict[str, Any]:
    """Return the YAML persona block for one named Clue character."""

    catalog = load_persona_catalog()
    characters = dict(catalog.get("characters") or {})
    profile = characters.get(str(character), {})
    return dict(profile) if isinstance(profile, dict) else {}


def persona_metric(character: str, field_name: str, *, default: int = 3) -> int:
    """Return one normalized 1-5 persona slider from the YAML profile."""

    profile = persona_profile(character)
    return _int_scale(profile.get(field_name), default=default) if profile else default


def persona_relationship_map(character: str) -> dict[str, dict[str, Any]]:
    """Return the normalized relationship hints for one character."""

    profile = persona_profile(character)
    relationships = dict(profile.get("relationships") or {}) if profile else {}
    normalized: dict[str, dict[str, Any]] = {}
    for other_name, payload in relationships.items():
        if not isinstance(payload, dict):
            continue
        normalized[str(other_name)] = dict(payload)
    return normalized


def persona_chat_examples(character: str) -> dict[str, list[str]]:
    """Return the normalized per-intent chat examples for one character."""

    profile = persona_profile(character)
    examples = dict(profile.get("chat_examples") or {}) if profile else {}
    normalized: dict[str, list[str]] = {}
    for intent_name, items in examples.items():
        normalized[str(intent_name)] = _string_list(items)
    return normalized


def build_persona_guidance(character: str) -> str:
    """Turn one YAML persona block into short LLM-facing guidance text.

    The richer freeform ``notes`` field is deliberately not injected verbatim
    into live prompts yet. For now the runtime uses the more structured fields so
    the YAML can influence play style without turning prompts into an unbounded
    character dossier.
    """

    profile = persona_profile(character)
    if not profile:
        return ""

    lines: list[str] = []

    public_tone = str(profile.get("public_chat_tone") or "").strip()
    if public_tone:
        lines.append(f"Public tone: {public_tone}")

    suggestion_style = str(profile.get("suggestion_style") or "").strip()
    if suggestion_style:
        lines.append(f"Suggestion style: {suggestion_style}")

    refute_style = str(profile.get("refute_style") or "").strip()
    if refute_style:
        lines.append(f"Refute style: {refute_style}")

    accusation_style = str(profile.get("accusation_style") or "").strip()
    if accusation_style:
        lines.append(f"Accusation style: {accusation_style}")

    directness = persona_metric(character, "directness", default=3)
    verbosity = persona_metric(character, "verbosity", default=3)
    deception = persona_metric(character, "deception", default=3)
    concealment = persona_metric(character, "concealment_priority", default=3)
    patience = persona_metric(character, "accusation_patience", default=3)
    risk = persona_metric(character, "risk_tolerance", default=3)

    if directness >= 4:
        lines.append("Speak directly rather than hedging.")
    elif directness <= 2:
        lines.append("Use implication and style more than blunt literal speech.")

    if verbosity <= 2:
        lines.append("Keep public chat brief.")
    elif verbosity >= 4:
        lines.append("You may talk a bit more than the other seats, but stay concise.")

    if deception >= 4:
        lines.append("You may use misleading but legal lines when they help conceal your knowledge.")
    elif deception <= 2:
        lines.append("Prefer sincere probes over elaborate feints.")

    if concealment >= 4:
        lines.append("Guard private knowledge carefully, especially in refutes and public chat.")

    if patience >= 4:
        lines.append("Delay accusations until the case feels well-supported.")
    elif patience <= 2:
        lines.append("Once the case sharpens, you do not need to linger before accusing.")

    if risk >= 4:
        lines.append("You are comfortable with bold pressure plays.")
    elif risk <= 2:
        lines.append("Avoid flashy or premature commitments.")

    return "\n".join(lines)


def persona_chattiness(character: str) -> int:
    """Return the configured 1-5 chattiness slider for one character."""

    return persona_metric(character, "chattiness", default=3)


def build_social_guidance(character: str) -> str:
    """Build richer chat-only guidance from the YAML persona profile."""

    profile = persona_profile(character)
    if not profile:
        return ""

    lines: list[str] = []
    public_tone = str(profile.get("public_chat_tone") or "").strip()
    if public_tone:
        lines.append(f"Public tone: {public_tone}")

    social_style = str(profile.get("social_style") or "").strip()
    if social_style:
        lines.append(f"Social style: {social_style}")

    lines.append(f"Chattiness: {persona_chattiness(character)}/5.")

    for move in _string_list(profile.get("signature_moves"))[:3]:
        lines.append(f"Signature move: {move}")

    for insecurity in _string_list(profile.get("insecurities"))[:2]:
        lines.append(f"Soft spot: {insecurity}")

    for taboo in _string_list(profile.get("taboos"))[:2]:
        lines.append(f"Avoid: {taboo}")

    notes = _string_list(profile.get("notes"))[:3]
    for note in notes:
        lines.append(f"Social cue: {note}")

    relationships = persona_relationship_map(character)
    for other_name, payload in list(sorted(relationships.items()))[:3]:
        stance = str(payload.get("stance") or "").strip()
        pressure_points = ", ".join(_string_list(payload.get("pressure_points"))[:2])
        if stance:
            lines.append(f"Toward {other_name}: {stance}.")
        if pressure_points:
            lines.append(f"Buttons with {other_name}: {pressure_points}.")

    examples = persona_chat_examples(character)
    for intent_name in ("tease", "deflect", "challenge", "ally", "reconcile", "meta_observe"):
        sample = next(iter(examples.get(intent_name) or []), "")
        if sample:
            lines.append(f"{intent_name.replace('_', ' ').title()} example: {sample}")

    return "\n".join(lines)


def model_profile(profile_id: str) -> dict[str, Any]:
    """Return one turn-decision model profile by id from ``models.yaml``."""

    return _catalog_profile(profile_id, kind="turn")


def chat_model_profile(profile_id: str) -> dict[str, Any]:
    """Return one chat-runtime model profile by id from ``models.yaml``."""

    return _catalog_profile(profile_id, kind="chat")


def model_runtime_defaults(*, kind: str = "turn") -> dict[str, Any]:
    """Return the maintainer-authored runtime defaults for turn or chat profiles."""

    section_name = "chat_runtime_defaults" if str(kind) == "chat" else "runtime_defaults"
    defaults = dict(load_model_catalog().get(section_name) or {})
    return defaults if isinstance(defaults, dict) else {}


def _catalog_profile(profile_id: str, *, kind: str) -> dict[str, Any]:
    """Read one named profile block from the turn or chat catalog section."""

    if not profile_id:
        return {}
    section_name = "chat_profiles" if str(kind) == "chat" else "profiles"
    profiles = dict(load_model_catalog().get(section_name) or {})
    profile = profiles.get(str(profile_id), {})
    return dict(profile) if isinstance(profile, dict) else {}


def assign_model_profiles(*, game_id: str, seats: list[Any]) -> dict[str, ModelProfileSelection]:
    """Choose deterministic turn-decision LLM profiles for eligible seats."""

    return _assign_profiles(game_id=game_id, seats=seats, kind="turn")


def assign_chat_model_profiles(*, game_id: str, seats: list[Any]) -> dict[str, ModelProfileSelection]:
    """Choose deterministic chat-runtime LLM profiles for eligible seats."""

    return _assign_profiles(game_id=game_id, seats=seats, kind="chat")


def _assign_profiles(*, game_id: str, seats: list[Any], kind: str) -> dict[str, ModelProfileSelection]:
    """Choose deterministic turn or chat profiles for LLM seats that need one."""

    catalog = load_model_catalog()
    section_name = "chat_profiles" if str(kind) == "chat" else "profiles"
    selection_name = "chat_selection" if str(kind) == "chat" else "selection"
    model_field = "agent_chat_model" if str(kind) == "chat" else "agent_model"
    profile_field = "agent_chat_profile" if str(kind) == "chat" else "agent_profile"
    profiles = {
        profile_id: dict(profile)
        for profile_id, profile in dict(catalog.get(section_name) or {}).items()
        if isinstance(profile, dict) and bool(profile.get("enabled", True))
    }
    if not profiles:
        return {}

    selection_cfg = dict(catalog.get(selection_name) or {})
    avoid_duplicates = bool(selection_cfg.get("avoid_duplicate_profiles_within_table", False))
    fallback_profile_id = str(selection_cfg.get("fallback_profile") or "")
    claimed: set[str] = set()
    assignments: dict[str, ModelProfileSelection] = {}

    for seat in seats:
        seat_kind = str(_seat_attr(seat, "seat_kind", "human")).strip().lower()
        if seat_kind != "llm":
            continue

        seat_id = str(_seat_attr(seat, "seat_id", "")).strip()
        character = str(_seat_attr(seat, "character", "")).strip()
        explicit_profile = str(_seat_attr(seat, profile_field, "")).strip()
        explicit_model = str(_seat_attr(seat, model_field, "")).strip()

        if explicit_profile:
            profile = profiles.get(explicit_profile) or profiles.get(fallback_profile_id)
            if profile:
                chosen_id = explicit_profile if explicit_profile in profiles else fallback_profile_id
                assignments[seat_id] = ModelProfileSelection(profile_id=chosen_id, profile=profile)
                claimed.add(chosen_id)
            continue

        if explicit_model:
            continue

        selected = _select_profile_for_seat(
            game_id=game_id,
            seat_id=seat_id,
            character=character,
            profiles=profiles,
            catalog=catalog,
            claimed_profiles=claimed if avoid_duplicates else set(),
            kind=kind,
        )
        if selected is None:
            continue
        assignments[seat_id] = selected
        claimed.add(selected.profile_id)

    return assignments


def _select_profile_for_seat(
    *,
    game_id: str,
    seat_id: str,
    character: str,
    profiles: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
    claimed_profiles: set[str],
    kind: str,
) -> ModelProfileSelection | None:
    """Choose one profile via deterministic weighted random selection."""

    candidates = _weighted_candidates(character=character, profiles=profiles, catalog=catalog, kind=kind)
    if claimed_profiles:
        unique_candidates = [item for item in candidates if item[0] not in claimed_profiles]
        if unique_candidates:
            candidates = unique_candidates
    if not candidates:
        return None

    selection_cfg = dict(catalog.get("chat_selection" if str(kind) == "chat" else "selection") or {})
    seed_basis = str(selection_cfg.get("seed_basis") or "game_id_and_seat_id")
    seed_material = f"{seed_basis}|{game_id}|{seat_id}|{character}"
    seed = int.from_bytes(hashlib.sha256(seed_material.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)

    total_weight = sum(weight for _, _, weight in candidates)
    threshold = rng.random() * total_weight
    running = 0.0
    for profile_id, profile, weight in candidates:
        running += weight
        if running >= threshold:
            return ModelProfileSelection(profile_id=profile_id, profile=profile)
    profile_id, profile, _weight = candidates[-1]
    return ModelProfileSelection(profile_id=profile_id, profile=profile)


def _weighted_candidates(
    *,
    character: str,
    profiles: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
    kind: str,
) -> list[tuple[str, dict[str, Any], float]]:
    """Return the enabled profile candidates with character-biased weights."""

    selection_cfg = dict(catalog.get("chat_selection" if str(kind) == "chat" else "selection") or {})
    bias_cfg = dict(catalog.get("chat_character_bias" if str(kind) == "chat" else "character_bias") or {})
    default_multiplier = _float_value(bias_cfg.get("default_multiplier"), default=1.0, minimum=0.0)
    apply_bias = bool(selection_cfg.get("apply_character_bias", False))
    character_biases = dict((bias_cfg.get("characters") or {}).get(str(character)) or {})

    weighted: list[tuple[str, dict[str, Any], float]] = []
    for profile_id, profile in sorted(profiles.items()):
        base_weight = _float_value(profile.get("weight"), default=1.0, minimum=0.0)
        multiplier = default_multiplier
        if apply_bias:
            multiplier = _float_value(character_biases.get(profile_id), default=default_multiplier, minimum=0.0)
        final_weight = base_weight * multiplier
        if final_weight <= 0.0:
            continue
        weighted.append((profile_id, profile, final_weight))
    return weighted


def _seat_attr(seat: Any, name: str, default: Any) -> Any:
    """Read one attribute from either a dataclass-like object or a mapping."""

    if isinstance(seat, dict):
        return seat.get(name, default)
    return getattr(seat, name, default)


def _int_scale(value: Any, *, default: int) -> int:
    """Normalize a 1-5 slider value with a safe default."""

    try:
        return min(max(int(value), 1), 5)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, *, default: float, minimum: float) -> float:
    """Parse one non-negative-ish numeric YAML field safely."""

    try:
        return max(float(value), minimum)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    """Normalize one YAML list-ish field into a trimmed string list."""

    return [str(item).strip() for item in list(value or []) if str(item).strip()]
