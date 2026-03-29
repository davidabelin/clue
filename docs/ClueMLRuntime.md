# Clue ML Runtime Guide

## Purpose
This document explains the **v1.5.0** OpenAI runtime inside the standalone `clue` repo. It is aimed at the maintainer who needs to change prompts, models, tools, tracing, or diagnostics without breaking Clue’s deterministic gameplay guarantees.

## Architectural Boundaries
- `clue_core` owns rules, legality, hidden state, turn progression, refutation flow, accusations, and filtered snapshots.
- `clue_agents` owns nonhuman seat policy selection.
- The OpenAI path may inspect seat-local context and return a structured decision, but it does **not** own state transitions.
- `clue_web.runtime.GameService` is the orchestration bridge. It builds snapshots, runs seat agents, records diagnostics, and then hands the normalized action back to `GameMaster`.

## Seat Runtime Overview

### Heuristic seats
- Deterministic baseline policy
- First fallback when the OpenAI path is unavailable or blocked
- Useful as a regression oracle and latency-safe backup

### LLM seats
- Implemented through the **OpenAI Agents SDK**
- Current default model snapshot: `gpt-5.4-mini-2026-03-17`
- Current default reasoning effort: `medium`
- Current default tool-call cap: `6`
- Current default max turns: `8`

## Context Model
Each LLM turn builds one code-owned `SeatAgentContext` object containing:
- seat-local filtered snapshot
- tool snapshot from the deduction engine
- accusation pacing gate
- local notebook excerpt
- trace id
- session id
- normalized runtime config

This context is passed into the SDK as the local run context. Tools and guardrails read from it directly. That design keeps private data under Clue’s control and avoids spreading seat-private logic into prompt-string assembly.

## Tool Inventory
The live seat agent exposes a bounded, read-only tool surface:
- `get_legal_action_envelope`
- `get_board_room_summary`
- `get_belief_summary`
- `get_top_hypotheses`
- `get_ranked_suggestions`
- `get_accusation_recommendation`
- `read_private_notebook`
- `inspect_move_target`
- `inspect_refute_card`

Rules for adding tools:
- Tools must be read-only.
- Tools must stay seat-local.
- Tools must not bypass the legal envelope.
- Tools must not expose another seat’s hidden hand or raw hidden game setup.
- Prefer summary tools over broad “dump everything” tools.

## Guardrails

### Output guardrail
The seat output guardrail blocks:
- actions outside the current legal envelope
- illegal move targets
- illegal refute cards
- missing required accusation or suggestion fields
- accusations before the pacing gate is ready
- unsafe public chat that looks like hidden-ownership leakage

When the output guardrail trips, the LLM decision is discarded and Clue falls back to the deterministic heuristic seat.

### Tool guardrails
Tool guardrails currently constrain parameterized tools:
- `inspect_move_target` only accepts targets from the current legal move set
- `inspect_refute_card` only accepts cards from the current private refute set

These guardrails reject out-of-scope arguments before the tool result is returned.

## Session Memory
- LLM seats use `EncryptedSession` over `SQLAlchemySession`
- Session key: `game_id:seat_id`
- Backend: local SQLite via `CLUE_AGENT_SESSION_DB_PATH`
- TTL: controlled by `CLUE_AGENT_SESSION_TTL_SECONDS`
- Encryption key: `CLUE_AGENT_SESSION_ENCRYPTION_KEY`, falling back to `CLUE_SECRET_KEY`

Operational intent:
- preserve seat-local short-term context across autonomous turns
- keep memory local to Clue by default
- make cleanup explicit on completed games while TTL handles stale leftovers

## Tracing And Diagnostics
- Runtime summary is stored in `analysis.agent_runtime`
- Seat-private debug data is stored in `analysis.latest_private_debug_by_seat`
- Browser diagnostics expose model, reasoning effort, trace id, session id, tool-call count, fallback reason, and guardrail results
- Sensitive trace data is off by default
- SDK tracing is configurable but not the default production path

What to inspect first when the LLM path misbehaves:
1. `analysis.seat_debug.decision.agent_meta`
2. `analysis.seat_debug.metric`
3. `analysis.seat_debug.decision_debug`
4. `analysis.seat_debug.tool_snapshot`

## Prompt And Model Changes
When changing the LLM runtime:
- prefer model snapshot changes over prompt rewrites when the existing contract is still sound
- keep prompts short and role-focused
- do not let prompts re-describe the full rules engine when tools already provide the relevant state
- preserve the rule that the model chooses one action, not a multi-step plan that mutates state directly
- keep public text optional and short

## Acceptance Checklist For Runtime Changes
- `pytest -q` stays green
- heuristic seats still behave identically for unchanged scenarios
- LLM seats cannot bypass legality checks
- another seat cannot see private debug or notebook state
- diagnostics remain readable from the browser
- deployment config still works with Secret Manager-backed API keys
