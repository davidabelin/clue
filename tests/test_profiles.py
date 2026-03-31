"""Tests for YAML-backed persona and model-profile integration."""

from __future__ import annotations

from pathlib import Path

from clue_agents import profile_loader
from clue_agents.llm import LLMSeatAgent
from clue_core.types import SeatConfig


def test_build_persona_guidance_uses_structured_yaml_fields_only(monkeypatch, tmp_path: Path):
    """Persona guidance should use stable style fields rather than raw freeform notes."""

    personas_path = tmp_path / "personas.yaml"
    personas_path.write_text(
        """
schema_version: 1
characters:
  "Miss Scarlet":
    public_chat_tone: "Sharp and theatrical."
    suggestion_style: "Pressure first."
    refute_style: "Hide information."
    accusation_style: "Accuse early."
    directness: 2
    verbosity: 4
    deception: 5
    concealment_priority: 5
    accusation_patience: 1
    risk_tolerance: 5
    notes:
      - "This freeform note should not be injected."
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(profile_loader, "PERSONAS_PATH", personas_path)
    profile_loader.clear_profile_caches()

    guidance = profile_loader.build_persona_guidance("Miss Scarlet")

    assert "Sharp and theatrical." in guidance
    assert "misleading but legal" in guidance
    assert "freeform note" not in guidance


def test_assign_model_profiles_respects_character_bias(monkeypatch, tmp_path: Path):
    """Character-biased weights should be able to force a unique profile choice."""

    models_path = tmp_path / "models.yaml"
    models_path.write_text(
        """
schema_version: 1
selection:
  apply_character_bias: true
  avoid_duplicate_profiles_within_table: true
  fallback_profile: "balanced"
character_bias:
  default_multiplier: 1.0
  characters:
    "Professor Plum":
      fast: 0.0
      balanced: 1.0
profiles:
  fast:
    enabled: true
    weight: 10
    model: "gpt-fast"
    public_label: "Fast"
    reasoning_effort: "low"
  balanced:
    enabled: true
    weight: 1
    model: "gpt-balanced"
    public_label: "Balanced"
    reasoning_effort: "medium"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(profile_loader, "MODELS_PATH", models_path)
    profile_loader.clear_profile_caches()

    seats = [
        SeatConfig(
            seat_id="seat_plum",
            display_name="Professor Plum",
            character="Professor Plum",
            seat_kind="llm",
        )
    ]
    assignments = profile_loader.assign_model_profiles(game_id="game_test", seats=seats)

    assert assignments["seat_plum"].profile_id == "balanced"
    assert assignments["seat_plum"].model == "gpt-balanced"


def test_llm_agent_profile_overrides_runtime_settings():
    """A selected YAML profile should override model and reasoning settings for one seat."""

    agent = LLMSeatAgent(api_key="", profile_id="gpt54_deep")

    assert agent._runtime_config.model == "gpt-5.4"
    assert agent._runtime_config.reasoning_effort == "high"
    assert agent._runtime_config.max_tool_calls == 7
    assert agent._profile_id == "gpt54_deep"
