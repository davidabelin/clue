# Clue Rolling TO DO List

## Current State After v1.7.0
- [x] Standalone Flask app plus AIX-mount-safe routing
- [x] Deterministic Clue rules engine with filtered seat snapshots
- [x] Mixed human and autonomous seats under one Game Master
- [x] SQL-backed persistence for games, seats, tokens, notebooks, and events
- [x] YAML-driven turn/chat profiles and persona-social guidance
- [x] OpenAI Agents SDK runtime with read-only tools, guardrails, local encrypted sessions, and heuristic fallback
- [x] Browser UI with polling synchronization and seat-private/public separation
- [x] Maintainer documentation and targeted docstring sweep completed for `v1.7.0`

## Highest Priority Backlog
- [ ] Add browser/API end-to-end coverage for a full human-only game, including reconnect after refresh or tab reopen.
- [ ] Add a multi-browser regression that proves separate seat tokens always restore the correct private view.
- [ ] Re-test deployed Clue against the shared Cloud SQL backend and then reassess whether the single-instance App Engine cap can be relaxed safely.
- [ ] Re-verify deployed Secret Manager resolution for `OPENAI_API_KEY` after each release.
- [ ] Add a small replay/eval harness so prompt or profile changes can be tested against stored traces and expected outcomes.

## Runtime And Gameplay Follow-Up
- [ ] Re-verify fallback behavior under live timeout and malformed-output scenarios, not just mocked tests.
- [ ] Re-check local and deployed latency budgets for deduction sampling plus LLM turns after future prompt/profile changes.
- [ ] Continue improving suggestion ranking and opponent-model hooks only if the change preserves the current rules/guardrail boundary.
- [ ] Decide whether an optional planner baseline should be added behind the existing `SeatAgent` interface.

## Research / Future Exploration
- [ ] Evaluate an optional ISMCTS baseline only after the replay/eval harness is in place.
- [ ] Consider POMCP or related belief-search work only if ISMCTS proves useful enough to justify the complexity.
- [ ] Export clean training traces only after browser E2E coverage and replay evaluation are stable.
- [ ] Consider supervised, DAgger, or population-style experiments only after the non-ML baseline and evaluation loop are trustworthy.

## Documentation And Process Defaults
- [x] Keep README plus `docs/` aligned with the shipped code rather than with an old plan.
- [x] Keep new public modules, classes, and functions maintainer-documented when added.
- [ ] When env defaults or runtime contracts change, update `README.md`, `docs/ClueMLRuntime.md`, and any version-sensitive tests in the same change.
