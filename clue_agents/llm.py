"""OpenAI-backed structured-output seat agent with heuristic fallback."""

from __future__ import annotations

import json
import os
from typing import Any

from clue_agents.base import SeatAgent, TurnDecision
from clue_agents.heuristic import HeuristicSeatAgent
from clue_agents.safety import sanitize_public_chat

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - import guard for local test envs
    OpenAI = None


class LLMSeatAgent(SeatAgent):
    def __init__(self, *, model: str = "", api_key: str = "") -> None:
        self._model = str(model or os.getenv("CLUE_LLM_MODEL", "gpt-4o-mini")).strip()
        self._api_key = str(api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self._fallback = HeuristicSeatAgent()

    def decide_turn(self, *, snapshot: dict[str, Any], tool_snapshot: dict[str, Any]) -> TurnDecision:
        if OpenAI is None or not self._api_key:
            return self._fallback.decide_turn(snapshot=snapshot, tool_snapshot=tool_snapshot)
        legal = snapshot["legal_actions"]
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a Clue seat agent. Obey legal actions exactly. Use the tool summary as ground truth. "
                    "Never claim hidden card ownership in public chat. Keep rationale_private brief."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Seat: {snapshot['seat']['display_name']} ({snapshot['seat']['character']})\n"
                    f"Phase: {snapshot['phase']}\n"
                    f"Legal actions: {legal}\n"
                    f"Private hand: {snapshot['seat']['hand']}\n"
                    f"Tool snapshot: {tool_snapshot}\n"
                    "Return the single best next action."
                ),
            },
        ]
        try:
            client = OpenAI(api_key=self._api_key)
            response = client.responses.create(
                model=self._model,
                input=prompt,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "turn_decision",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string"},
                                "target_node": {"type": ["string", "null"]},
                                "suspect": {"type": ["string", "null"]},
                                "weapon": {"type": ["string", "null"]},
                                "room": {"type": ["string", "null"]},
                                "card": {"type": ["string", "null"]},
                                "text": {"type": ["string", "null"]},
                                "rationale_private": {"type": "string"},
                            },
                            "required": ["action", "rationale_private"],
                            "additionalProperties": False,
                        },
                    }
                },
            )
            raw = json.loads(str(getattr(response, "output_text", "") or "{}"))
            decision = TurnDecision.from_dict(raw)
            if not decision.action:
                return self._fallback.decide_turn(snapshot=snapshot, tool_snapshot=tool_snapshot)
            if decision.text:
                decision.text = sanitize_public_chat(decision.text)
            return decision
        except Exception:
            return self._fallback.decide_turn(snapshot=snapshot, tool_snapshot=tool_snapshot)
