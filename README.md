# clue

Standalone Clue lab for AIX, currently labeled **v1.9.0**.

## What This Repo Is
- Classic Clue rules implemented as a deterministic, event-sourced Python engine.
- A standalone Flask web app that also mounts cleanly under AIX at `/clue`.
- Mixed human and autonomous seats under one code-owned Game Master.
- Local-first OpenAI integration: the model can inspect filtered seat context and write durable NHP memory/social notes, but it never mutates rules state directly.

## Core Invariants
- `clue_core` remains authoritative for legality, turn order, refutation flow, accusations, and win/loss state.
- Autonomous seats may only return a normalized `TurnDecision` or `ChatDecision`; model-facing write tools are limited to durable NHP memory/social state.
- Seat-private information is filtered server-side before it reaches the browser.
- Public chat is sanitized and guardrailed before LLM-authored text is accepted.
- Local encrypted session memory is seat-scoped and separate from the persisted game-state database.
- Durable NHP memory is persisted cross-game for agent runtime use and Administrator Mode, never normal player snapshots.

## Repo Layout
- `clue_core/`: board model, constants, setup, events, deterministic rules engine, deduction-facing types, release metadata
- `clue_agents/`: seat interfaces, heuristic policy, Agents SDK integration, prompt/policy helpers, YAML profile loading, runtime config, safety helpers, secret resolution
- `clue_storage/`: SQLAlchemy-backed persistence for games, seats, notebooks, tokens, event history, durable NHP memory, note/audit rows, and relationships
- `clue_web/`: Flask app factory, runtime service, routes, templates, and browser assets
- `tests/`: unit and integration-style coverage for engine, profiles, runtime config, app/API flow, heuristic play, and LLM wrappers
- `docs/`: maintainer documentation, architecture notes, backlog, and release history
- `data/`: local SQLite files for gameplay state and encrypted seat-agent sessions

## Runtime Shape

### 1. Game creation
- `clue_web.runtime.GameService.create_game()` validates seat payloads, normalizes legacy seat kinds, applies YAML-selected LLM profiles where needed, builds hidden setup, persists initial state, and may immediately run autonomous opening turns.
- Seat UI mode is persisted as `beginner` or `player`; omitted mode defaults to Beginner, and `superplayer` is reserved for a later release.
- Each new table receives a fresh setup seed so deals and case files are not repeated from one global default.

### 2. Web request surface
- `/` renders the create-game page.
- `/join/<token>` marks a seat invite as used and redirects to `/game`.
- `/game?token=...` renders the seat-specific shell; `clue.js` hydrates state from JSON.
- `/game` includes a UI-only Quit Game link back to Clue Home; it does not mutate game state.
- `/api/v1/games/current` returns the filtered snapshot, including the table UI mode, for the current signed seat token.
- `/api/v1/games/current/actions` applies one action through the Game Master, persists state and events, and runs any follow-up autonomous turns.
- `/api/v1/games/current/notebook` updates one seat-private notebook.
- `/admin` renders the protected Superplayer administration entry screen, linked from Clue Home and the shared chrome.
- `/admin?admin_token=...` renders the Superplayer administration dashboard for saved-game review, stats, NHP memory, durable notes, relationships, and player histories.
- `/admin/games/<game_id>?admin_token=...` renders full admin-truth game inspection, including the case file, hands, private events, traces, metrics, social state, memory, and notes.
- `/api/v1/admin/...` exposes the same protected data surfaces plus memory retry and session runtime settings for maintainers.

### 3. Rules authority
- `clue_core.engine.GameMaster` validates every action and emits public or seat-private events.
- `clue_core.engine.build_filtered_snapshot()` is the privacy boundary for browser and agent-visible state.

### 4. Autonomous seats
- `clue_agents.runtime.AgentRuntime` instantiates heuristic or LLM-backed seats behind one shared interface.
- `clue_core.deduction.build_tool_snapshot()` prepares the seat-local deduction summary used by both heuristic and LLM policies.
- `clue_agents.llm.LLMSeatAgent` uses the OpenAI Agents SDK with read tools, tightly bounded durable write tools, and output guardrails. If the live LLM path is unavailable or invalid, the seat fails loudly instead of using the heuristic policy.
- Idle chat uses a separate chat profile path and a two-stage intent-plus-utterance run. Reactive chat remains first; proactive quiet-table chat is throttled once per turn.
- Completed games create durable LLM-authored memory jobs for each NHP. Ready memory is loaded into future NHP runtime snapshots; missing SDK/API credentials leave jobs pending for admin retry.

## Environment Contract

### App and storage
- `CLUE_SECRET_KEY`
  Flask secret key and fallback session-encryption seed for local development.
- `CLUE_SECRET_KEY_SECRET`
  Secret Manager indirection for `CLUE_SECRET_KEY`/Flask `SECRET_KEY`.
- `CLUE_DATABASE_URL`
  Preferred SQLAlchemy database URL for game state.
- `CLUE_DATABASE_URL_SECRET`
  Secret Manager indirection for `CLUE_DATABASE_URL`.
- `CLUE_DB_PATH`
  Local SQLite fallback when `CLUE_DATABASE_URL` is unset.
- `APP_BASE_PATH`
  Mount prefix such as `/clue` when running behind AIX dispatch.
- `AIX_HUB_URL`
  Base URL used to build shared AIX chrome links.
- `CLUE_INTERNAL_WORKER_TOKEN`
  Optional shared token for the internal run-agents endpoint.
- `CLUE_ADMIN_TOKEN`
  Required token for `/admin` and `/api/v1/admin/...` saved-game and NHP-memory surfaces.
- `CLUE_ADMIN_TOKEN_SECRET`
  Secret Manager indirection for `CLUE_ADMIN_TOKEN`.

### LLM runtime
- `CLUE_LLM_MODEL`
  Default: `gpt-5.4-mini-2026-03-17`
- `CLUE_LLM_REASONING_EFFORT`
  Default: `medium`
- `CLUE_LLM_TIMEOUT_SECONDS`
  Default: `12` locally; `app.aix.yaml` sets `45`
- `CLUE_LLM_MAX_TOOL_CALLS`
  Default: `6` locally; `app.aix.yaml` sets `12`
- `CLUE_AGENT_MAX_TURNS`
  Default: `8` locally; `app.aix.yaml` sets `18`
- `CLUE_AGENT_TRACING_ENABLED`
  Default: `0`
- `CLUE_AGENT_TRACE_INCLUDE_SENSITIVE_DATA`
  Default: `0`
- `CLUE_AGENT_SESSION_TTL_SECONDS`
  Default: `900`
- `CLUE_AGENT_SESSION_DB_PATH`
  Default: `data/clue_agent_sessions.db` locally, `/tmp/clue_agent_sessions.db` in `app.aix.yaml`
- `CLUE_AGENT_SESSION_ENCRYPTION_KEY`
  Falls back to `CLUE_SECRET_KEY`, then a dev default
- `CLUE_AGENT_EVAL_EXPORT_ENABLED`
  Default: `0`
- `CLUE_IDLE_CHAT_ENABLED`
  Default: `1`; set `0` to disable all optional snapshot-triggered NHP idle chat.
- `CLUE_PROACTIVE_CHAT_ENABLED`
  Default: `1`; set `0` to disable quiet-table proactive NHP chat.
- `CLUE_PROACTIVE_CHAT_CHANCE_MULTIPLIER`
  Default: `0.35`; clamps proactive chat chance without affecting reactive replies.
- `OPENAI_API_KEY`
  Direct local override for the OpenAI API key
- `OPENAI_API_KEY_SECRET_VERSION`
  Secret Manager indirection for deployment

The Superplayer administration dashboard can also apply process-local overrides for idle chat, proactive chat, and proactive chat chance. Those overrides reset on app restart and do not change the stored environment contract.

Model-profile and chat-profile YAML defaults live in:
- [`clue_agents/profiles/models.yaml`](./clue_agents/profiles/models.yaml)
- [`clue_agents/profiles/personas.yaml`](./clue_agents/profiles/personas.yaml)

## Local Run
```powershell
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:5002/`.

For local admin review:

```powershell
$env:CLUE_ADMIN_TOKEN = "local-admin"
python run.py
```

Open `http://127.0.0.1:5002/admin` and enter `local-admin`. The production route is `https://aix-labs.uw.r.appspot.com/clue/admin` and uses the deployed `clue-admin-token` Secret Manager value.

## Tests
```powershell
pytest -q
```

## Deployment Notes
- `app.aix.yaml` runs the app behind Gunicorn on App Engine.
- `app.smoke.yaml` deploys a separate `clue-smoke` App Engine service for live smoke tests against an isolated smoke database secret.
- Game state can live in SQLite or PostgreSQL/Cloud SQL through `CLUE_DATABASE_URL`.
- The production deployed config expects Secret Manager-backed `clue-database-url`, `clue-secret-key`, `clue-admin-token`, and `openai-api-key`.
- The smoke deployed config expects `clue-smoke-database-url`, `clue-smoke-secret-key`, and `clue-smoke-admin-token`; write-based smoke checks must use `clue-smoke`, not production Clue storage.
- `APP_BASE_PATH=/clue` plus `PathPrefixMiddleware` keeps standalone routes mount-safe under AIX.

## Documentation Map
- Start with [`docs/README.md`](./docs/README.md) for the maintainer doc index.
- Use [`docs/ClueMLRuntime.md`](./docs/ClueMLRuntime.md) for OpenAI runtime, guardrail, and session behavior.
- Use [`docs/CLUE_live_checks.md`](./docs/CLUE_live_checks.md) for local, smoke-service, and production read-only verification.
- Use [`docs/ClueDeepDive.md`](./docs/ClueDeepDive.md) for the end-to-end architecture walkthrough.
- Use [`docs/CLUE_PLAN_alpha.md`](./docs/CLUE_PLAN_alpha.md) for the implementation-history and architecture-decision record.
- Use [`docs/clue_to_do.md`](./docs/clue_to_do.md) for the current post-`v1.9.0` backlog.
- Use [`docs/CHANGELOG.md`](./docs/CHANGELOG.md) for release history.
