# Durable NHP Statefulness Step Toward v1.8.0

## Summary
Implement durable, cross-game NHP memory as a code-owned persistence/runtime feature, with LLM-authored memory summaries only. Game completion must create one memory job per NHP, attempt the LLM summary once, queue failures for admin retry, and load ready memory into future NHP turn/chat decisions without exposing it in normal player snapshots.

This pass includes backend, admin API/page, tests, docs, and docstrings. It does not change gameplay UX decisions or bump `VERSION` yet.

## Key Changes
- Add durable memory storage in `clue_storage`:
  - `nhp_memory`: one row per completed game + NHP, keyed by canonical character identity, status `pending|ready|failed`, source game/seat, LLM-authored `summary_json`, model metadata, failure reason, retry count, timestamps.
  - `nhp_relationships`: durable NHP-to-NHP and NHP-to-HP relationship rows keyed by `agent_identity`, `target_kind`, and `target_identity`, with bounded social scores, notes, last source game, timestamps.
  - Repository methods for listing games, creating/fetching/updating memory jobs, relationship upserts, pending-memory lookup, and admin summaries.
- Add LLM memory-summary mode:
  - New structured output type `MemorySummaryOutput` with first-person summary, strategic lessons, social observations, relationship updates, grudges/favors, and future-play cues.
  - New SDK mode `memory_summary` using read-only tools for final game context, prior ready memory, relationship history, and seat-visible event history.
  - No deterministic summary fallback. If SDK/API/model fails, leave the job `pending` or `failed` for admin retry.
- Extend runtime data flow:
  - On game completion, create memory jobs after final state/events are saved.
  - Attempt each NHP memory job once immediately; mark unresolved jobs pending without blocking game completion.
  - Load ready memory + durable relationships into `memory_context` for future NHP turn, idle-chat, and memory-summary runs.
  - Seed per-game social relationship scores from durable relationships when a new game starts, while preserving existing YAML persona relationship hints.
  - Keep durable memory out of normal filtered player snapshots.
- Add admin surface protected by `CLUE_ADMIN_TOKEN`:
  - Header: `X-Clue-Admin-Token`
  - Query param for browser page: `admin_token`
  - `GET /admin` renders a plain maintainer page for saved games, NHP memory, pending jobs, and durable relationships.
  - `GET /api/v1/admin/games`, `GET /api/v1/admin/games/<game_id>`, `GET /api/v1/admin/nhp-memory`, `GET /api/v1/admin/relationships`.
  - `POST /api/v1/admin/nhp-memory/retry` retries pending/failed memory jobs.
- Update docs/docstrings:
  - `README.md`: env contract, admin access, durable NHP memory.
  - `docs/ClueMLRuntime.md`: memory-summary mode, failure model, tool/privacy rules.
  - `docs/ClueDeepDive.md`: durable memory storage and admin flow.
  - `docs/CHANGELOG.md`: add an unreleased entry for the statefulness work.
  - Add docstrings for every new public class/function and important private orchestration helper.

## Implementation Details
- Identity rules:
  - NHP identity is the canonical character name, e.g. `Miss Scarlet`.
  - HP identity is normalized display name: trimmed, casefolded, whitespace-collapsed; store original display name for admin readability.
  - Seat IDs remain game-scoped and are only source references.
- Memory lifecycle:
  - Only `ready` rows are loaded into future games.
  - `pending` rows appear in admin and can be retried.
  - `failed` rows retain failure reason and retry count; admin retry can move them back through processing.
  - Existing game completion must stay idempotent: repeated snapshot/action cleanup paths must not create duplicate memory rows.
- Privacy boundary:
  - Durable memory is available only through NHP runtime context/tools and admin endpoints.
  - Browser seat snapshots never include durable memory, even for an LLM seat token.
  - Admin game detail may expose hidden setup and private traces because it is token-protected maintainer mode.
- Versioning:
  - Do not bump `VERSION` or `clue_core.version.CLUE_VERSION` in this step.
  - Prepare docs/changelog so a later release pass can bump to `1.8.0` once the user accepts behavior.

## Test Plan
- Run `pytest -q`.
- Add repository tests for schema creation, memory job idempotence, pending/ready/failed transitions, relationship upserts, game listing, and SQLite compatibility.
- Add runtime tests:
  - completed game creates one memory job per NHP;
  - mocked successful LLM summary writes `ready` memory and relationship updates;
  - missing API key queues pending memory without blocking game completion;
  - future NHP decisions receive ready memory in `memory_context`;
  - normal player snapshots do not expose durable memory.
- Add SDK/LLM wrapper tests:
  - memory-summary success records model/session/tool metadata;
  - malformed or unsafe memory output fails the job cleanly;
  - memory mode uses read-only tools only.
- Add admin tests:
  - no/invalid admin token is rejected;
  - valid token can list games, memory, relationships, and retry pending jobs;
  - admin page renders with configured token.
- Re-run existing LLM, idle-chat, social-memory, and mixed-seat full-game tests to catch interface regressions.

## Assumptions
- “LLM-authored only” means no deterministic prose or structured memory substitute is stored when the model is unavailable; pending jobs are acceptable.
- “Full admin surface” means a protected maintainer API plus a plain data-first admin page, not a polished Superplayer UX.
- The next implementation pass should include a copy-paste-ready Markdown commit summary after code/docs/tests are changed.
