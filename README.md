# clue

Standalone Clue lab for AIX, currently labeled **v1.5.0**.

## Summary
- Deterministic, event-sourced classic Clue rules engine
- Seat-token multiplayer web UI with polling-based synchronization
- Human, heuristic, and OpenAI-backed autonomous seats under one rules authority
- Flask app that runs standalone and also mounts under AIX at `/clue`
- Local-first OpenAI integration: the model path is tool-augmented and stateful, but the rules engine, private seat data, notebooks, diagnostics, and session memory remain code-owned by Clue

## v1.5.0 Architecture

### Core invariant
The **Game Master** remains authoritative. No model, prompt, tool, or session store is allowed to mutate gameplay state directly. Autonomous seats can only return a normalized `TurnDecision`, which is then validated and applied by the deterministic rules engine.

### Autonomous seat stack
- `heuristic` seats remain the baseline nonhuman policy and the universal fallback
- `llm` seats now run through the **OpenAI Agents SDK**
- LLM seats use **read-only function tools** to inspect the legal envelope, belief summary, ranked suggestions, accusation recommendation, notebook excerpt, and selected move/refute details
- LLM outputs are validated by **output guardrails** before any decision reaches the rules engine
- Tool argument scope is constrained by **tool guardrails**
- Seat-private short-term memory uses an **EncryptedSession** over a local SQLAlchemy-backed SQLite store

### Privacy posture
- Private seat state is filtered server-side before it reaches the browser
- Private notebooks remain seat-local
- Local encrypted session memory is keyed by `game_id:seat_id`
- OpenAI-hosted stored responses and hosted sessions are **not** the default production path
- SDK tracing is configurable, but sensitive trace data is **off by default**

## Repo Layout
- `clue_core/`: deterministic rules engine, board, setup, events, filtering, version marker
- `clue_agents/`: heuristic and Agents-SDK seat runtimes, safety helpers, secret resolution, ML runtime config
- `clue_storage/`: SQLAlchemy persistence facade for games, seats, tokens, notebooks, and events
- `clue_web/`: Flask app factory, runtime service, routes, templates, static assets
- `tests/`: engine, app, heuristic, deduction, and LLM-seat coverage
- `docs/`: design notes, roadmap material, maintainer-facing ML/runtime documentation
- `data/`: local SQLite databases and the local encrypted agent-session store

## Key Environment Variables
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
  Default: `data/clue_agent_sessions.db`
- `CLUE_AGENT_SESSION_ENCRYPTION_KEY`
  Default: falls back to `CLUE_SECRET_KEY`
- `CLUE_AGENT_EVAL_EXPORT_ENABLED`
  Default: `0`
- `OPENAI_API_KEY`
  Direct local override for the OpenAI API key
- `OPENAI_API_KEY_SECRET_VERSION`
  Secret Manager indirection for deployment

## Local Run
```powershell
pip install -r requirements.txt
python run.py
```

Then open `http://127.0.0.1:5002/`.

## Tests
```powershell
pytest -q
```

## Maintainer Notes
- Start with [`docs/ClueMLRuntime.md`](./docs/ClueMLRuntime.md) for the v1.5.0 OpenAI runtime design.
- Historical design and roadmap material remains in `docs/ClueDeepDive.md`, `docs/CLUE_PLAN_alpha.md`, and `docs/clue_to_do.md`.
- The standalone `clue` repo currently contains **no `.bat` files**. Batch helper documentation therefore remains N/A here unless such scripts are added later.
