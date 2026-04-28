# Clue Deep Dive

## Overview
`clue` is a standalone, event-sourced Clue implementation with a Flask UI, a deterministic rules engine, a SQL-backed persistence layer, and optional autonomous seats. The system is designed so that maintainers can evolve the seat policies, profiles, prompts, and diagnostics without weakening the authority of the rules engine or the privacy boundary around seat-private state.

The fastest way to orient yourself is:
1. Read the top-level [`README.md`](../README.md).
2. Read this document for the end-to-end flow.
3. Read [`ClueMLRuntime.md`](./ClueMLRuntime.md) if you are touching the OpenAI path.

## Subsystem Map

### `clue_core`
Owns immutable game facts and deterministic gameplay behavior:
- board graph and secret passages
- card constants and categories
- hidden setup generation
- event helpers
- the `GameMaster`
- filtered snapshot construction
- seat-local deduction helpers

### `clue_agents`
Owns autonomous-seat behavior:
- seat interfaces and normalized decision types
- heuristic baseline policy
- OpenAI Agents SDK wrapper
- persona and model-profile YAML loading
- prompt/policy composition
- secret and runtime-config loading
- public-chat sanitization

### `clue_storage`
Owns persistence:
- game record creation
- seat and token rows
- notebook persistence
- append-only event history
- loading and saving the current state snapshot
- durable NHP memory jobs and cross-game relationship rows
- append-only durable NHP note/audit rows

### `clue_web`
Owns app assembly and request orchestration:
- Flask app factory and mount-safe configuration
- JSON and HTML routes
- `GameService`, which ties together storage, the `GameMaster`, deduction snapshots, agent runs, metrics, and social memory
- browser assets and templates

## Authoritative Data Model

### Persisted game record
Each game stores:
- config JSON
- hidden setup JSON
- current mutable state JSON
- append-only events

The current state is authoritative for serving the latest snapshot. The event log exists for player-facing history, diagnostics, and replay-style reasoning.

### Seat records
Each seat stores:
- stable seat key scoped to a game
- character and display name
- normalized seat kind
- selected agent model/profile metadata
- private notebook JSON
- first-seen timestamp

### Seat tokens
Humans do not log in with full accounts in v1. A signed seat token maps to one persisted seat row and is the only credential needed for the seat-specific game view and API calls.

## Request And Gameplay Flow

### Game creation
`POST /api/v1/games`
- validates the requested seats
- normalizes legacy `heuristic` payloads into `llm` at the public contract level
- requires 3 to 6 active seats
- selects deterministic YAML turn/chat profiles for LLM seats when not explicitly set
- builds hidden setup and initial state
- persists game, seats, tokens, and opening events
- may immediately run autonomous opening turns until a human response is needed

### Join flow
`/join/<token>`
- records first-seen time for the seat
- redirects onto `/game?token=...`

### Game page flow
`/game`
- renders a static shell plus the signed seat token
- `clue.js` polls `/api/v1/games/current`
- server responses are authoritative; the browser preserves only draft UI state such as notebook text, chat text, and action selections

### Human action flow
`POST /api/v1/games/current/actions`
- resolves and validates the signed seat token
- loads the current state
- applies the action through `GameMaster.apply_action()`
- persists new state and emitted events
- runs any queued autonomous follow-up turns
- returns a newly filtered snapshot for the acting seat

### Notebook flow
`POST /api/v1/games/current/notebook`
- updates only the seat's notebook JSON
- returns a fresh filtered snapshot

### Administrator flow
`/admin?admin_token=...` and `/api/v1/admin/...`
- require `CLUE_ADMIN_TOKEN`
- expose the Superplayer administration dashboard, saved-game summaries, full admin-truth game detail, durable NHP memory, durable notes/tool writes, durable relationships, HP/NHP history, memory-job retry, and session runtime settings
- are maintainer surfaces, not normal player views

`/admin/games/<game_id>?admin_token=...` renders one saved game with case-file truth, all hands, private events, private traces, metrics, social state, memory, and notes. The admin token is therefore a full-trust surface.

## Rules Engine And Snapshot Boundary

### `GameMaster`
The `GameMaster` is intentionally narrow and deterministic:
- validates the action against turn ownership and phase
- computes the legal action envelope
- applies rolls, moves, suggestions, refutations, accusations, and chat
- advances turn order
- emits public or seat-private events

It does not know about Flask, SQLAlchemy, the browser, or the OpenAI runtime.

### Filtered snapshots
`build_filtered_snapshot()` is the privacy boundary used by both browser and agent paths.

The filtered snapshot includes:
- public seat state
- the current seat's private hand and notebook
- the seat-visible event stream
- legal actions for that seat
- analysis data with seat-private debug filtered per seat
- social-memory slices that are safe for the current seat to see

This function is where public/private routing must remain explicit and easy to audit.
Durable cross-game memory is intentionally not part of normal filtered snapshots. `GameService` injects `memory_context` only after building internal NHP runtime snapshots.

## Deduction Layer
`clue_core.deduction.ClueBeliefTracker` maintains seat-local constraints over card ownership.

Capabilities:
- exact owner elimination and propagation where possible
- clause-style tracking from public refutations
- sampling of consistent complete deals
- envelope marginals and joint hypotheses
- suggestion ranking with repeat penalties and leak-aware opponent penalties
- accusation recommendations
- compact debug summaries for autonomous seats and browser diagnostics

This deduction layer feeds both:
- the heuristic policy
- the LLM tool snapshot

That shared summary is deliberate. The repo keeps one code-owned reasoning substrate so that the heuristic and LLM paths see the same legal and probabilistic picture.

## Autonomous Seat Stack

### Heuristic path
The heuristic seat remains the simplest end-to-end autonomous path. It is deterministic enough to serve as:
- baseline play behavior
- an explicit internal policy where stored legacy state still names it
- regression anchor when the LLM path changes

### LLM path
`LLMSeatAgent` wraps the OpenAI Agents SDK while keeping the integration narrow:
- one code-owned context object
- typed output contracts
- read tools plus narrow durable memory/social write tools
- output and tool guardrails
- local encrypted session memory
- fail-loud handling when the SDK, API key, model call, or structured output fails

The LLM may recommend a move, suggestion, accusation, refutation, or optional public text. It does not apply the action itself.

### Profile loading
`clue_agents.profile_loader` reads two maintainer-authored YAML files:
- `models.yaml`
  turn profiles, chat profiles, and runtime defaults
- `personas.yaml`
  character voice, social posture, relationship hints, and example chat patterns

The loader is intentionally forgiving. Missing or malformed YAML degrades toward hardcoded runtime behavior rather than breaking gameplay startup.

## Social Memory
`GameService` persists a code-owned social state for non-human seats.

Tracked concepts include:
- mood
- focus seat
- speaking streak
- cooldown state
- recent chat intents
- relationship scores
- active side threads

The model can influence this layer through structured chat output and the bounded `update_relationship_posture` write tool. The persisted social state is normalized and bounded in code before it is written back to the game state.

## Durable NHP Memory
Completed games create a durable memory job for each non-human seat.

Memory identity rules:
- NHP memory is keyed by canonical Clue character name.
- Human-player relationship targets are keyed by normalized display name.
- Seat ids remain game-scoped source references.

Memory lifecycle:
- `pending`: queued or waiting for an available LLM runtime.
- `ready`: LLM-authored memory is available to future NHP runs.
- `failed`: model/runtime output failed and can be retried from Administrator Mode.

Ready memory and durable relationship posture are loaded into future NHP turn, chat, and memory-summary runs. New games also use durable relationships as small social-posture nudges while preserving the YAML persona relationship hints.
Append-only `nhp_notes` rows capture durable observations, social intent notes, and write-tool audit history. Recent notes are injected only into internal NHP memory context and Administrator Mode.

## Storage Model
`ClueRepository` is a thin SQLAlchemy facade that supports both:
- local SQLite
- PostgreSQL / Cloud SQL through `CLUE_DATABASE_URL`

Important persistence properties:
- schema is created programmatically
- seat ids are scoped per game through a `seat_key`
- notebook state is stored per seat
- events are appended with a monotonically increasing `event_index`
- the repository stores opaque JSON payloads for config, setup, state, and notebooks rather than enforcing a relational gameplay schema
- durable NHP memory and relationships are relational enough for admin lookup and cross-game agent context
- durable NHP notes are append-only so model-facing writes remain audit-visible even if the final model output fails

That tradeoff keeps the application logic in Python, where the privacy and rules semantics already live.

## Browser Runtime
The browser is intentionally light:
- no SPA framework
- server-rendered HTML shell
- one JS file that owns polling, DOM updates, and draft-state preservation
- SVG board rendering

`clue.js` follows two important rules:
- never trust client state over server state
- never lose in-progress human text or dropdown choices during polling unless a successful server response supersedes them

This is why notebook text, chat text, and action dropdown drafts are tracked separately from the latest snapshot.

## Privacy And Security Model

### Seat tokens
- signed with `itsdangerous`
- scoped to one game and seat
- checked against persisted token rows

### Browser privacy
- browser snapshots are pre-filtered server-side
- another seat's private hand or notebook is never sent to the client
- durable NHP memory is not sent to normal player snapshots

### LLM privacy
- read tools are seat-local; write tools are limited to durable NHP memory/social state
- public chat is sanitized
- sensitive tracing is off by default
- SDK response storage is enabled for active session continuity, while durable cross-game memory remains in Clue-owned storage
- durable memory tools are internal to autonomous-seat runs and Administrator Mode

### Deployment secrets
- `CLUE_DATABASE_URL_SECRET` can fill the database URL from Secret Manager
- `OPENAI_API_KEY_SECRET_VERSION` can fill the OpenAI API key from Secret Manager
- `CLUE_SECRET_KEY_SECRET` can fill the Flask/seat-token signing secret from Secret Manager
- `CLUE_ADMIN_TOKEN_SECRET` can fill the Administrator Mode token from Secret Manager
- `CLUE_ADMIN_TOKEN` protects Administrator Mode and admin APIs

## Testing Strategy
Current tests cover:
- core engine legality and gameplay behavior
- deduction behavior
- profile loading
- runtime config and guardrails
- app and API flow
- heuristic and LLM wrapper behavior

The suite is strongest at backend correctness and runtime safety. The main remaining gap is fuller browser end-to-end coverage across reconnect and multi-window flows.

## Where To Make Changes
- gameplay legality, phases, turn flow:
  `clue_core.engine`
- filtered snapshot shape or private/public routing:
  `clue_core.engine` and `clue_web.runtime`
- seat heuristics, prompts, or profile-selection behavior:
  `clue_agents`
- persistence and database support:
  `clue_storage.repository`
- route wiring, orchestration, metrics, and social memory:
  `clue_web.runtime`
- browser layout or polling UX:
  `clue_web/templates/` and `clue_web/static/js/clue.js`

## Design Biases Worth Preserving
- keep rules authority separate from model behavior
- keep privacy boundaries explicit in code, not implicit in prompts
- keep the browser thin and server-authoritative
- keep heuristic behavior healthy as a separate regression baseline
- keep runtime docs synchronized with actual env defaults and profile behavior
