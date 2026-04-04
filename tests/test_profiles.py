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


def test_build_social_guidance_includes_chattiness_and_top_notes(monkeypatch, tmp_path: Path):
    """Chat-only persona guidance should include chattiness and a short note slice."""

    personas_path = tmp_path / "personas.yaml"
    personas_path.write_text(
        """
schema_version: 1
characters:
  "Mrs. Peacock":
    public_chat_tone: "Refined and strategic."
    social_style: "Treats the table as a salon."
    chattiness: 5
    signature_moves:
      - "Turns politeness into a weapon."
    relationships:
      "Miss Scarlet":
        stance: "Competitive glamour rivalry."
        chemistry: 2
        pressure_points:
          - "Scarlet's vanity"
    chat_examples:
      tease:
        - "Miss Scarlet, confidence and sophistication do remain separate qualities."
    notes:
      - "Treats the table as a social battlefield."
      - "Very aware of appearances."
      - "Sounds polite while maneuvering."
      - "This fourth note should be omitted."
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(profile_loader, "PERSONAS_PATH", personas_path)
    profile_loader.clear_profile_caches()

    guidance = profile_loader.build_social_guidance("Mrs. Peacock")

    assert "Refined and strategic." in guidance
    assert "Treats the table as a salon." in guidance
    assert "Chattiness: 5/5." in guidance
    assert "Turns politeness into a weapon." in guidance
    assert "Competitive glamour rivalry." in guidance
    assert "confidence and sophistication" in guidance
    assert "social battlefield" in guidance
    assert "appearances" in guidance
    assert "maneuvering" in guidance
    assert "fourth note" not in guidance
    assert profile_loader.persona_chattiness("Mrs. Peacock") == 5


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


def test_assign_chat_model_profiles_uses_chat_catalog(monkeypatch, tmp_path: Path):
    """Chat-profile selection should use the separate chat catalog sections."""

    models_path = tmp_path / "models.yaml"
    models_path.write_text(
        """
schema_version: 1
selection:
  apply_character_bias: false
profiles:
  turn_only:
    enabled: true
    weight: 1
    model: "gpt-turn"
chat_selection:
  apply_character_bias: true
  avoid_duplicate_profiles_within_table: false
  fallback_profile: "chat_balanced"
chat_character_bias:
  default_multiplier: 1.0
  characters:
    "Miss Scarlet":
      chat_balanced: 0.0
      chat_expressive: 5.0
chat_profiles:
  chat_balanced:
    enabled: true
    weight: 1
    model: "gpt-chat-balanced"
    public_label: "Chat Balanced"
    reasoning_effort: "medium"
  chat_expressive:
    enabled: true
    weight: 1
    model: "gpt-chat-expressive"
    public_label: "Chat Expressive"
    reasoning_effort: "high"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(profile_loader, "MODELS_PATH", models_path)
    profile_loader.clear_profile_caches()

    seats = [
        SeatConfig(
            seat_id="seat_scarlet",
            display_name="Miss Scarlet",
            character="Miss Scarlet",
            seat_kind="llm",
        )
    ]
    assignments = profile_loader.assign_chat_model_profiles(game_id="game_test", seats=seats)

    assert assignments["seat_scarlet"].profile_id == "chat_expressive"
    assert assignments["seat_scarlet"].model == "gpt-chat-expressive"


def test_llm_agent_profile_overrides_runtime_settings():
    """A selected YAML profile should override model and reasoning settings for one seat."""

    agent = LLMSeatAgent(api_key="", profile_id="gpt54_deep")

    assert agent._runtime_config.model == "gpt-5.4"
    assert agent._runtime_config.reasoning_effort == "high"
    assert agent._runtime_config.max_tool_calls == 7
    assert agent._profile_id == "gpt54_deep"
