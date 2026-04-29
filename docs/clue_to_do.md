# Clue TO DO List

- As of 2026-04-29
- Current version: 1.9.0
- Next target: v2.0.0 release candidate in about two weeks

## Rolling items

This list stays at the top for loose hints and breadcrumbs. Move items into the structured lists below as soon as they become actionable.

No rolling items are currently loose; the previous notes were integrated into the v1.9.0 stabilization pass.

## v1.9.0 Stabilization Pass

Goal: make the existing v1.8.0 chatbot/admin/gameplay surfaces easier to operate, clearer when the live LLM path fails, less laggy during polling, and visually tighter without introducing a major new feature.

- [x] Admin token discoverability: explain on `/admin` that local tokens come from `CLUE_ADMIN_TOKEN`, deployed tokens can come from `CLUE_ADMIN_TOKEN_SECRET`, and production uses the `clue-admin-token` Secret Manager value.
- [x] LLM failure visibility: keep the public `llm_unavailable` gameplay update concise for all seats, and expand Beginner diagnostics with failure reason, affected seat, latency, fallback status, and private trace detail only for the affected seat.
- [x] Lag/performance pass: add browser fetch/render timing and skip unchanged board, seat, event, diagnostics, and explainer redraws during polling.
- [x] Beginner UI polish: trim duplicate helper copy, slightly rebalance Caseboard/Table Wire proportions, and improve low-risk SVG board line rendering.
- [x] Regression coverage: prove admin token guidance does not expose the token and LLM failure debug remains private while public failure events stay visible.
- [x] Release docs and markers: bump the release label to `v1.9.0`, update the changelog, and keep live-check docs current.

## v2.0.0 Release-Candidate Work

### Superplayer / Admin UX

- [ ] Expand Superplayer Mode into a fuller administrative / Dungeon Master style UX with richer NHP/player controls.
- [ ] Add future NHP rankings after a ranking model is defined.
- [ ] Preserve Superplayer hooks in planning and docstrings where relevant.
- [ ] Keep the admin surface full-trust and token-protected; never expose admin data to normal seat snapshots.

### Gameplay Flow And Persistence

- [ ] Add Pause, Save, and Resume game controls after persistence semantics are explicitly defined.
- [ ] Decide whether save/resume is only a UI affordance over existing SQL persistence or a new named-slot workflow.
- [ ] Keep Quit Game as UI-only unless the game lifecycle contract changes.

### Browser And UX Stabilization

- [ ] Add browser/API end-to-end coverage for a full human-only game, including reconnect after refresh or tab reopen.
- [ ] Add a multi-browser regression that proves separate seat tokens always restore the correct private view.
- [ ] Review fresh cache-busted Beginner and Player screenshots before the v2.0.0 release candidate.
- [ ] Continue board-art polish if jagged/irregular lines remain after the v1.9.0 low-risk SVG cleanup.

### Runtime Evaluation

- [ ] Add a small replay/eval harness so prompt or profile changes can be tested against stored traces and expected outcomes.
- [ ] Re-verify fail-loud LLM behavior under live timeout and malformed-output scenarios, not just mocked tests.
- [ ] Continue improving suggestion ranking and opponent-model hooks only if the change preserves the current rules/guardrail boundary.
- [ ] Decide whether an optional planner baseline should be added behind the existing `SeatAgent` interface.

### Deployment

- [ ] Run deployed smoke/backend checks against `clue-smoke`, then production read-only checks.
- [ ] Re-test deployed Clue against the shared Cloud SQL backend and reassess whether the single-instance App Engine cap can be relaxed safely.
- [ ] Re-verify deployed Secret Manager resolution for `OPENAI_API_KEY`, `CLUE_ADMIN_TOKEN`, and Flask signing secret after each release.

## Current Shipped State

- [x] Standalone Flask app plus AIX-mount-safe routing.
- [x] Deterministic Clue rules engine with filtered seat snapshots.
- [x] Mixed human and autonomous seats under one Game Master.
- [x] SQL-backed persistence for games, seats, tokens, notebooks, events, durable memory, durable relationships, and append-only NHP notes.
- [x] YAML-driven turn/chat profiles and persona-social guidance.
- [x] OpenAI Agents SDK runtime with read tools, durable memory/social write tools, guardrails, local encrypted sessions, and fail-loud LLM turn/chat handling.
- [x] Reactive and proactive NHP chat behavior with bounded cooldown/throttle handling.
- [x] Browser UI with polling synchronization, client-side draft preservation, and seat-private/public separation.
- [x] Beginner and Player table UI modes.
- [x] Superplayer Admin entry, dashboard, saved-game inspection, NHP/player history, memory retry, and runtime chat controls.
- [x] Fresh setup seeds for new games, so deals and case files are no longer repeated from a fixed default.
- [x] Maintainer documentation updated through the `v1.9.0` stabilization release.

## Research / Later Exploration

- [ ] Evaluate an optional ISMCTS baseline only after the replay/eval harness is in place.
- [ ] Consider POMCP or related belief-search work only if ISMCTS proves useful enough to justify the complexity.
- [ ] Export clean training traces only after browser E2E coverage and replay evaluation are stable.
- [ ] Consider supervised, DAgger, or population-style experiments only after the non-ML baseline and evaluation loop are trustworthy.

## Process Defaults

- [x] Keep README plus `docs/` aligned with the shipped code rather than with an old plan.
- [x] Keep new public modules, classes, and functions maintainer-documented when added.
- [ ] When env defaults or runtime contracts change, update `README.md`, `docs/ClueMLRuntime.md`, and any version-sensitive tests in the same change.
- [ ] After any non-trivial repo change, compose a copy-paste-ready uncompiled Markdown commit summary.
