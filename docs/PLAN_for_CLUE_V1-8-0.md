# Clue v1.8.0 Full Chatbot Release Plan

## Summary
Ship v1.8.0 as one release push: live NHP chatbot behavior, durable memory/relationship continuity, explicit model-facing write tools, admin visibility, and deployed proof without writing to the production Clue database.

Current baseline: local `pytest -q` is green (`73 passed`), durable memory/admin scaffolding already exists, and the latest local commit made deployed LLM seats fail loudly instead of falling back to heuristics. `VERSION` is currently dirty at `1.7.8`; treat that as user state and only finalize both release markers at the end.

## Key Changes
- Add Secret Manager support for `CLUE_ADMIN_TOKEN_SECRET` and `CLUE_SECRET_KEY_SECRET`, mirroring the existing database secret pattern, so production admin access and signed seat tokens are not blank/dev-default.
- Add a separate `clue-smoke` App Engine manifest using an isolated Cloud SQL database/secret, so live OpenAI/App Engine/Cloud SQL smoke tests can create games without touching production Clue game data.
- Add canonical immediate write tools for LLM seats:
  - `record_memory_note` persists durable NHP observations immediately.
  - `update_relationship_posture` immediately updates bounded `nhp_relationships` and records an audit note.
  - `record_social_intent_note` persists the NHP’s intended pressure/alliance/deflection context for admin and future memory.
- Keep all write tools outside rules authority: they may update durable NHP memory/social state, but never hidden setup, legal actions, hands, turn order, or game outcome.
- Extend persistence with an append-only NHP note/audit table loaded into `memory_context` and exposed in Admin Mode.
- Enable proactive chatbot behavior with bounded cooldowns: NHPs may speak during quiet periods when no autonomous rules action is pending, with a per-game/per-turn throttle and model-chosen silence still respected.
- Add admin history endpoints/pages for NHP histories, human player histories by normalized display name, durable memory notes, relationships, saved games, and retryable memory jobs.
- Update prompts/profiles so turn, chat, proactive chat, and memory-summary runs are explicitly memory-aware, relationship-aware, and character-specific.
- Finalize docs/changelog and bump both `VERSION` and `clue_core.version.CLUE_VERSION` to `1.8.0` only after deployed smoke proof passes.

## Implementation Steps
1. Release plumbing:
   - Add secret resolution for admin and Flask secret keys.
   - Add `app.smoke.yaml` for service `clue-smoke`, with isolated `clue_smoke` DB secret and no dispatch changes required.
   - Add docs for production vs smoke URLs and the no-prod-writes verification rule.

2. Write-tool runtime:
   - Add a repository-backed write sink passed from `GameService` into `AgentRuntime`, `LLMSeatAgent`, and `SeatAgentContext`.
   - Implement SDK write tools in `clue_agents.sdk_runtime`.
   - Persist writes immediately, bounded and validated server-side, and include tool-write diagnostics in private traces/admin views.

3. Chatbot behavior:
   - Keep current reactive idle chat.
   - Add proactive idle chat when the table is quiet, throttled by env-configurable cooldowns.
   - Fold durable relationship changes back into the current social context before subsequent NHP runs.
   - Ensure failed LLM runs still preserve immediate write-tool audit rows.

4. Admin/history surfaces:
   - Add repository methods and admin APIs for NHP history, HP history, durable notes, relationship history, and game summaries.
   - Keep normal player snapshots free of durable memory/write-tool data.

5. Verification and release:
   - Install current requirements in the local runtime used for live checks.
   - Run mocked/local tests, then local live OpenAI smoke tests against SQLite.
   - Deploy `clue-smoke` and run live App Engine/OpenAI/Cloud SQL smoke checks there.
   - Run production read-only checks only: service health, admin auth, config/secret resolution, and existing production DB readability.
   - Bump to `1.8.0`, update docs, and include a copy-paste-ready Markdown commit summary.

## Test Plan
- Existing suite must stay green: `pytest -q`.
- Add repository tests for durable note/audit persistence, canonical relationship writes, HP/NHP history queries, and smoke DB compatibility.
- Add SDK/runtime tests proving write tools validate targets, clamp deltas, persist immediately, and do not expose private hidden data.
- Add failure tests proving immediate writes remain audit-visible if final model output fails.
- Add proactive chat tests for cooldown, no pending NHP action, model silence, and no repeated chatter loops.
- Add admin tests for token secret resolution, saved games, NHP/HP history, durable notes, relationships, and memory retry.
- Add live-check scripts/docs for local OpenAI smoke, `clue-smoke` deployed smoke, and production read-only verification.

## Assumptions
- v1.8.0 means one complete release push, not a partial epsilon follow-up.
- Model-facing write tools should update canonical durable relationship/social memory immediately.
- Deployed write-based proof must use an isolated smoke database, not the production `clue` database.
- Proactive chat should be present but bounded, not constant table noise.
- Heuristic code remains only as a test/legacy baseline, never as a fake LLM fallback.
