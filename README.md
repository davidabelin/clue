# clue

Standalone Clue lab for AIX, currently labeled **v1.6.1**.

## What This Repo Is
- Classic Clue rules implemented as a deterministic, event-sourced Python engine.
- A standalone Flask web app that also mounts cleanly under AIX at `/clue`.
- Mixed human and autonomous seats under one code-owned Game Master.
- Local-first OpenAI integration: the model can inspect filtered, read-only seat context, but it never mutates gameplay state directly.

## Core Invariants
- `clue_core` remains authoritative for legality, turn order, refutation flow, accusations, and win/loss state.
- Autonomous seats may only return a normalized `TurnDecision` or `ChatDecision`.
- Seat-private information is filtered server-side before it reaches the browser.
- Public chat is sanitized and guardrailed before LLM-authored text is accepted.
- Local encrypted session memory is seat-scoped and separate from the persisted game-state database.

## Repo Layout
- `clue_core/`: board model, constants, setup, events, deterministic rules engine, deduction-facing types, release metadata
- `clue_agents/`: seat interfaces, heuristic policy, Agents SDK integration, prompt/policy helpers, YAML profile loading, runtime config, safety helpers, secret resolution
- `clue_storage/`: SQLAlchemy-backed persistence for games, seats, notebooks, tokens, and event history
- `clue_web/`: Flask app factory, runtime service, routes, templates, and browser assets
- `tests/`: unit and integration-style coverage for engine, profiles, runtime config, app/API flow, heuristic play, and LLM wrappers
- `docs/`: maintainer documentation, architecture notes, backlog, and release history
- `data/`: local SQLite files for gameplay state and encrypted seat-agent sessions

## Runtime Shape

### 1. Game creation
- `clue_web.runtime.GameService.create_game()` validates seat payloads, normalizes legacy seat kinds, applies YAML-selected LLM profiles where needed, builds hidden setup, persists initial state, and may immediately run autonomous opening turns.

### 2. Web request surface
- `/` renders the create-game page.
- `/join/<token>` marks a seat invite as used and redirects to `/game`.
- `/game?token=...` renders the seat-specific shell; `clue.js` hydrates state from JSON.
- `/api/v1/games/current` returns the filtered snapshot for the current signed seat token.
- `/api/v1/games/current/actions` applies one action through the Game Master, persists state and events, and runs any follow-up autonomous turns.
- `/api/v1/games/current/notebook` updates one seat-private notebook.

### 3. Rules authority
- `clue_core.engine.GameMaster` validates every action and emits public or seat-private events.
- `clue_core.engine.build_filtered_snapshot()` is the privacy boundary for browser and agent-visible state.

### 4. Autonomous seats
- `clue_agents.runtime.AgentRuntime` instantiates heuristic or LLM-backed seats behind one shared interface.
- `clue_core.deduction.build_tool_snapshot()` prepares the seat-local deduction summary used by both heuristic and LLM policies.
- `clue_agents.llm.LLMSeatAgent` uses the OpenAI Agents SDK with read-only tools, output guardrails, and deterministic fallback to the heuristic policy.
- Idle chat uses a separate chat profile path and a two-stage intent-plus-utterance run.

## Environment Contract

### App and storage
- `CLUE_SECRET_KEY`
  Flask secret key and fallback session-encryption seed for local development.
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

### LLM runtime
- `CLUE_LLM_MODEL`
  Default: `gpt-5.4-mini-2026-03-17`
- `CLUE_LLM_REASONING_EFFORT`
  Default: `medium`
- `CLUE_LLM_TIMEOUT_SECONDS`
  Default: `12`
- `CLUE_LLM_MAX_TOOL_CALLS`
  Default: `6`
- `CLUE_AGENT_MAX_TURNS`
  Default: `8`
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
- `OPENAI_API_KEY`
  Direct local override for the OpenAI API key
- `OPENAI_API_KEY_SECRET_VERSION`
  Secret Manager indirection for deployment

Model-profile and chat-profile YAML defaults live in:
- [`clue_agents/profiles/models.yaml`](./clue_agents/profiles/models.yaml)
- [`clue_agents/profiles/personas.yaml`](./clue_agents/profiles/personas.yaml)

## Local Run
```powershell
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:5002/`.

## Tests
```powershell
pytest -q
```

## Deployment Notes
- `app.aix.yaml` runs the app behind Gunicorn on App Engine.
- Game state can live in SQLite or PostgreSQL/Cloud SQL through `CLUE_DATABASE_URL`.
- The deployed config expects Secret Manager-backed `clue-database-url` and `openai-api-key`.
- `APP_BASE_PATH=/clue` plus `PathPrefixMiddleware` keeps standalone routes mount-safe under AIX.

## Documentation Map
- Start with [`docs/README.md`](./docs/README.md) for the maintainer doc index.
- Use [`docs/ClueMLRuntime.md`](./docs/ClueMLRuntime.md) for OpenAI runtime, guardrail, and session behavior.
- Use [`docs/ClueDeepDive.md`](./docs/ClueDeepDive.md) for the end-to-end architecture walkthrough.
- Use [`docs/CLUE_PLAN_alpha.md`](./docs/CLUE_PLAN_alpha.md) for the implementation-history and architecture-decision record.
- Use [`docs/clue_to_do.md`](./docs/clue_to_do.md) for the current post-`v1.6.1` backlog.
- Use [`docs/CHANGELOG.md`](./docs/CHANGELOG.md) for release history.
