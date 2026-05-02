# Clue ML Runtime Guide

## Purpose
This document describes the current OpenAI seat runtime for the v1.9.x stabilization line. It is for the maintainer changing prompts, profiles, tools, tracing, failure behavior, or social-memory integration without breaking Clue's deterministic gameplay guarantees.

## Runtime Boundary
- `clue_core` owns rules, hidden setup, legality, turn progression, refutation order, accusations, and filtered snapshots.
- `clue_web.runtime.GameService` owns orchestration: snapshot building, tool-snapshot generation, seat-agent execution, diagnostics, and persistence.
- `clue_agents` owns autonomous seat policy selection.
- The model-facing path can inspect seat-local context and return structured output. Its write tools may persist durable NHP memory/social state only; they never mutate gameplay state directly.
- The `GameMaster` remains the only authority that applies actions.

## Seat Runtime Topology

### Heuristic seats
- Deterministic baseline policy
- Used as an explicit autonomous policy only when a stored seat is truly configured as heuristic
- Also acts as a regression oracle for tests and internal comparisons

### LLM seats
- Implemented through the OpenAI Agents SDK
- Wrapped by `clue_agents.llm.LLMSeatAgent`
- Driven by one turn profile and one chat profile, both selected from `clue_agents/profiles/models.yaml`
- Grounded by a shared deduction snapshot built by `clue_core.deduction.build_tool_snapshot()`
- Turn decisions run behind a process-local autonomous worker; create-table and human-action responses queue work, return a filtered snapshot, and let polling expose public-safe progress through `analysis.autonomous_work`
- Persona prompts prepend the shared `table_voice` block from `personas.yaml`, so all NHPs play as modern partygoers knowingly role-playing Clue suspects with concise, ironic Christie-ish banter

### Idle chat path
- Chat is separate from gameplay decisions and is disabled by default for v1.9.1 stabilization
- `GameService.maybe_run_idle_chat()` decides whether one non-human seat may speak after new player-facing public activity
- If all new public activity has been processed and no autonomous rules action is pending, proactive quiet-table chat may run once per game turn
- The chat runtime uses one compact SDK output, `AgentChatOutput`, which either chooses silence or returns one safe public line
- Chat relationship writes can still happen through durable write tools, but structured chat `relationship_deltas` are not part of the v1.9.1 optional-chat output path
- Chat session ids are separate from turn session ids: `game_id:seat_id:chat`

### Durable memory path
- Completed games create one durable memory job per NHP seat.
- The memory runtime uses `MemorySummaryOutput` and writes first-person, LLM-authored summaries only.
- Memory-summary session ids are separate again: `game_id:seat_id:memory`.
- Missing SDK/API credentials leave the job pending for Administrator Mode retry; there is no heuristic prose fallback.

## Runtime Configuration

### App-level defaults
`clue_web.create_app()` exposes the runtime contract through Flask config and environment variables.

### LLM defaults
`clue_agents.config.load_llm_runtime_config()` normalizes the seat runtime into one cached `LLMRuntimeConfig`.

Current defaults:
- model: `gpt-5.4-mini-2026-03-17`
- reasoning effort: `medium`
- base env timeout: `12` seconds locally; AIX deployment sets `45` seconds
- YAML turn-profile default budget: `30` seconds, `12` tool calls, `18` SDK turns
- YAML chat-profile default budget: `12` seconds, `4` tool calls, `6` SDK turns
- Optional chat agent output budget: one compact `AgentChatOutput` with a low token cap
- tracing enabled: `false`
- sensitive trace data: `false`
- session TTL: `900` seconds
- local session DB path: `data/clue_agent_sessions.db` unless overridden

### Admin session overrides
Superplayer Administrator Mode can set process-local overrides for optional idle chat:
- all snapshot-triggered idle chat enabled/disabled
- proactive quiet-table chat enabled/disabled
- proactive quiet-table chat chance multiplier

The env defaults are `CLUE_IDLE_CHAT_ENABLED=0` and `CLUE_PROACTIVE_CHAT_ENABLED=0`. Start diagnostics with both toggles off, measure core turn latency, then enable optional chat only for chatter-specific testing. These overrides reset on app restart. They do not change stored game state, model profiles, API keys, or the rules engine.

### Profile precedence
- Runtime config starts with normalized env defaults.
- `model_runtime_defaults(kind="turn")` or `model_runtime_defaults(kind="chat")` overlays maintainer-authored YAML defaults.
- A selected seat profile overlays those defaults.
- A direct `model` or `chat_model` override wins last.

This precedence keeps deployment knobs centralized while still allowing deterministic per-seat profile selection.

## Seat Context Model
Each SDK run gets a code-owned `SeatAgentContext` containing:
- the filtered seat snapshot
- the deduction/tool snapshot
- the accusation pacing gate
- the trace id and local session id
- chat mode, if the optional single-pass chat path is running
- durable memory context injected only for internal NHP runtime calls
- the repository-backed write sink for durable NHP memory/social writes
- tool-access logs for diagnostics
- tool-write logs for private traces and admin inspection

The context is the source of truth for tools and guardrails. This avoids pushing seat-private logic into prompt strings and keeps the privacy boundary code-owned.

## Tool Surface
Most tools are read-only. Examples include:
- legal action envelope
- board and room summary
- belief summary and top hypotheses
- ranked suggestions
- accusation recommendation
- private notebook excerpt
- move-target and refute-card inspection
- social-memory summaries, active threads, and recent public history
- durable cross-game memory and relationship context
- final-game context for memory-summary runs

The v1.9.0 write tools are deliberately narrow and are exposed to chat and durable-memory paths, not to the compact core turn-action agent:
- `record_memory_note`
  appends a durable first-person NHP note.
- `update_relationship_posture`
  updates a bounded durable relationship row and writes an audit note.
- `record_social_intent_note`
  appends the NHP's intended pressure, alliance, deflection, or table-context note.

Rules for future tools:
- keep them seat-local
- keep write tools outside rules authority
- do not bypass the legal envelope
- do not expose another seat's private hand or the raw hidden setup
- prefer compact summaries over bulk dumps

## Guardrails

### Output guardrails
The output guardrails reject or trip on:
- illegal actions
- illegal move targets or refute cards
- missing required accusation or suggestion fields
- accusations before the pacing gate allows them
- unsafe public chat that looks like hidden-ownership leakage
- invalid legacy chat intent enums or target seat ids
- obviously fabricated public-fact references that contradict recent visible history
- empty durable memory summaries or relationship updates without targets

When output validation fails for a turn, the LLM decision is discarded and the runtime records an explicit LLM failure instead of generating a heuristic move. The compact chat path may still choose deliberate silence for model-authored no-speak or unsafe-public-chat outcomes; it does not use heuristic chat text as a substitute.
Memory-summary validation failures mark the durable memory job failed or pending; they do not synthesize replacement memory.

The turn-action agent keeps a smaller surface than the social/memory agents: read-only rules, deduction, notebook, movement/refute inspection, and social context tools. Tool choice is automatic rather than forced, and the prompt asks the model to return promptly with a short private rationale once it has a legal action. This reduces loops and truncated structured output for verbose/high-reasoning personas while preserving the fail-loud policy.

### Tool guardrails
Parameterized tools are constrained before they execute:
- move-target inspection only accepts currently legal move targets
- refute-card inspection only accepts currently legal private refute cards

These checks keep the model from probing outside the current legal envelope through tool arguments.

## Session Memory
- Backend: `EncryptedSession` over `SQLAlchemySession`
- Storage target: local SQLite file from `CLUE_AGENT_SESSION_DB_PATH`
- Turn session id: `game_id:seat_id`
- Chat session id: `game_id:seat_id:chat`
- Memory summary session id: `game_id:seat_id:memory`
- Encryption key: `CLUE_AGENT_SESSION_ENCRYPTION_KEY`, then `CLUE_SECRET_KEY`, then a dev fallback
- SDK response storage: enabled, because the Agents SDK session can reference prior response items across turn/chat calls

Operational intent:
- preserve seat-local short-term memory across autonomous turns
- keep the canonical durable memory in Clue storage while allowing SDK response continuity for active sessions
- make cleanup explicit when a game completes while TTL handles stale leftovers

`GameService._cleanup_llm_sessions_if_complete()` is the post-game cleanup hook.

## Diagnostics And Telemetry
Per-game analysis lives in `state["analysis"]` and is persisted with the game state.

Important sections:
- `analysis.run_context`
  environment, release label, seat mix, and seat order
- `analysis.agent_runtime`
  summarized runtime contract from `AgentRuntime.runtime_summary()`
- `analysis.game_metrics`
  aggregate counters such as explicit LLM failures, fallback count for legacy metrics, guardrail blocks, completion rate, and max latencies
- `analysis.turn_metrics`
  bounded rolling record of recent turn-level metrics
- `analysis.latest_private_debug_by_seat`
  seat-private debug material that only appears in the matching filtered snapshot

Useful browser-facing diagnostics include:
- model and reasoning effort
- trace id
- session id
- tool-call count
- LLM failure reason when the live model path cannot produce a turn
- belief summary and top-ranked suggestions

Superplayer Administrator Mode exposes fuller diagnostics, including saved-game summaries, case-file truth, all hands, private events, traces, turn metrics, social state, durable memory, notes, and relationships.

## Social Memory Integration
The LLM runtime does not own the social graph. `GameService` does.

Persisted social state includes:
- per-seat mood, cooldown, speaking streak, and recent intents
- bounded relationship scores for affinity, trust, and friction
- active and cooling side threads with topic, participants, heat, and status

The model may propose structured relationship deltas and thread actions, but the canonical social state is normalized and bounded in `GameService` before persistence.

## Durable Cross-Game Memory
Durable NHP memory lives in repository tables rather than the encrypted SDK session store.

- `nhp_memory` stores one memory job per completed game and NHP seat.
- `nhp_relationships` stores bounded cross-game relationship posture keyed by canonical NHP character and either canonical NHP target or normalized HP display name.
- `nhp_notes` stores append-only memory/social/tool-audit notes and is loaded into `memory_context.recent_notes`.
- Only `ready` memory rows are loaded into future NHP turn/chat/memory runs.
- `pending` and `failed` rows are visible through Administrator Mode and can be retried.
- Normal player snapshots never include durable memory; `GameService._build_internal_snapshot()` injects it only for autonomous runtime calls.

## Failure Model
The LLM path is intentionally fail-loud.

Failure causes include:
- Agents SDK unavailable
- missing API key
- timeout
- guardrail block
- malformed or illegal output
- unsafe public text
- general model/runtime exceptions

Turn failures raise `LLMDecisionError`. `GameService.maybe_run_agents()` catches that error, records an `llm_unavailable` public event plus a seat-private trace, and leaves the game on that NHP turn. No heuristic move is produced.
When an NHP turn cannot finish, the autonomous worker marks `analysis.autonomous_work.status` as `blocked` or `error` with only a short public-safe reason; private runtime detail remains in seat-private traces and Administrator Mode.
Chat runtime failures also raise `LLMDecisionError`; idle-chat orchestration records a seat-private `trace_llm_unavailable` event and posts no substitute chat. Administrator Mode counts those optional-chat traces separately from gameplay-turn `llm_unavailable_count`, with per-game breakdowns by seat, model/profile, reason, and sample error prefix. Deliberate model silence remains a valid non-error outcome.
Durable memory model/runtime failure leaves a retryable memory job.

## What To Check Before Changing This Runtime
1. Does the change preserve the rule that only the Game Master mutates game state?
2. Does the change keep read tools read-only, write tools durable-memory-only, and all tools seat-local?
3. Does the change preserve filtered private/public visibility boundaries?
4. Does the change preserve fail-loud LLM behavior without heuristic substitution?
5. Do the env defaults, docs, and tests all still match?

## Acceptance Checklist For Runtime Changes
- `pytest -q` stays green
- heuristic behavior remains testable as an explicit policy, not as an LLM substitute
- LLM seats cannot bypass legality or privacy checks
- seat-private debug data remains private
- durable write-tool rows are visible in Administrator Mode but not normal player snapshots
- session storage stays local-first unless explicitly redesigned
- deployment still works with the Clue-specific Secret Manager-backed OpenAI key and database URLs
