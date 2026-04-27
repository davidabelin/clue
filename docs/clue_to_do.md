# Clue TO DO List

- As of 2026-04-27
- Current version: 1.8.0
- Next target: 1.8.x stabilization and Superplayer prep

## 1.8.0 Milestone Definition

Clue reaches `v1.8.0` when the Non-Human Players are operating in full chatbot mode:

- [x] Every NHP has durable state across games and never feels stateless or unsure about what they are doing or why.
- [x] Every NHP retains character, voice, personal goals, relationships, grudges, favors, and relevant table history.
- [x] NHPs play to win strategically while also using social behavior to influence, mislead, help, bargain with, or pressure other NHPs and HPs.
- [x] NHP-to-NHP and NHP-to-HP relationships can develop progressively across repeated games.
- [x] Game history, player history, and interaction history are persisted, retrievable, and organized enough to support both gameplay and Administrator Mode.
- [x] The shipped behavior is covered by targeted tests or replay/eval checks so future prompt/profile/runtime changes can be judged against saved traces.

## 1.8.0 Priority Plan: Full-Bore Cluebot Mode

### 1. Inventory Current NHP Runtime

- [ ] Locate the current NHP turn/chat implementation and identify exactly where chatbot behavior is still heuristic, placeholder, stateless, or disconnected from persona profiles.
- [ ] Confirm how YAML turn/chat profiles, persona-social guidance, OpenAI Agents SDK runtime, tools, guardrails, local encrypted sessions, and explicit LLM failure paths currently connect.
- [ ] Identify which data already exists in SQL persistence for games, seats, tokens, notebooks, events, and autonomous-player turns.
- [ ] Document the gap between current persisted data and the durable memory/social state required for `v1.8.0`.

### 2. Durable NHP Memory

- [ ] Define the memory record shape for each NHP after every game:
  - game outcome and personal performance
  - clues learned, deductions made, and strategic mistakes
  - promises, favors, betrayals, alliances, rivalries, and social impressions
  - notable interactions with HPs and NHPs
  - persona-relevant emotional or behavioral takeaways
- [ ] Add storage for per-NHP memory summaries and relationship state.
- [ ] Require each NHP to compose or update a memory summary at game end.
- [ ] Load relevant memory summaries into each NHP's starting state in future games.
- [ ] Add retention controls so memory stays useful instead of becoming unbounded prompt clutter.

### 3. Social Relationship Model

- [ ] Add relationship state between every pair of seats or recurring named players:
  - trust
  - suspicion
  - owed favors
  - grudges
  - alliance tendency
  - recent interaction notes
- [ ] Make the relationship state visible to NHP reasoning without exposing private information illegally.
- [ ] Update relationship state from suggestions, disprovals, accusations, chat, negotiations, and game outcomes.
- [ ] Ensure social behavior can target HPs as well as other NHPs.

### 4. NHP Tools And Required Tool Use

- [ ] Identify the missing tools NHPs need for full chatbot mode.
- [ ] Add read tools for personal memory, relationship state, public table history, and legal current-turn context.
- [ ] Add write tools for memory summaries, relationship updates, and social intent notes where appropriate.
- [ ] Define when tool use is mandatory before an NHP responds or acts.
- [ ] Keep all tools inside the existing rules/guardrail boundary so NHPs cannot access hidden information they should not know.

### 5. In-Character Strategic And Social Turns

- [ ] Rewrite or extend NHP prompts/profiles so each turn combines:
  - legal Clue strategy
  - persona voice
  - memory-aware continuity
  - relationship-aware social behavior
  - clear intent for public chat, private reasoning, and game action
- [ ] Replace rote placeholder NPC dialogue with table-aware, character-specific chat.
- [ ] Add social tactics that are legal within the game:
  - pressure
  - bluffing
  - favor trading
  - selective helpfulness
  - rivalry escalation
  - alliance signaling
- [ ] Ensure live LLM failures are visible and actionable instead of replaced by heuristic dialogue or moves.

### 6. Save Games And Administrator Mode Foundation

- [ ] Save all games with important game stats, player histories, and interaction histories.
- [ ] Make saved games retrievable and functionally organized for Administrator Mode.
- [ ] Add Administrator Mode views or API endpoints for:
  - game statistics and accumulated analysis
  - saved game browsing and inspection
  - NHP histories, configurations, rankings, and relationship records
  - HP histories organized by the names human players give to the table
- [ ] Keep Administrator Mode planning aligned with eventual Superplayer Mode.

### 7. Verification And Release Readiness

- [ ] Add or update replay/eval coverage so prompt/profile changes can be tested against stored traces and expected outcomes.
- [ ] Add targeted tests for memory creation, memory loading, relationship updates, and private/public information boundaries.
- [ ] Re-verify fail-loud LLM behavior under live timeout and malformed-output scenarios.
- [ ] Re-check local and deployed latency budgets after memory and social-state prompts are added.
- [ ] Re-verify deployed Secret Manager resolution for `OPENAI_API_KEY`.
- [ ] Re-test deployed Clue against the shared Cloud SQL backend before release.
- [ ] Update README, runtime docs, and version-sensitive tests before bumping `VERSION` to `1.8.0`.

## Immediate Work Queue

1. [x] Audit the current NHP implementation and persistence schema.
2. [x] Design the minimum durable memory + relationship schema needed for `v1.8.0`.
3. [x] Implement memory write at game end and memory load at game start.
4. [x] Add NHP read/write tools for memory and relationship state.
5. [x] Update NHP prompts/profiles to require memory-aware, in-character, social play.
6. [x] Replace remaining placeholder NPC dialogue paths with reactive plus proactive LLM chat behavior.
7. [x] Add focused tests/replay checks for the new runtime surface.
8. [x] Add Administrator Mode access to saved games and NHP/player history data.
9. [ ] Run deployed smoke/backend checks against `clue-smoke`, then production read-only checks.
10. [x] Update docs and bump `VERSION` to `1.8.0`.

## UI Modes And Gameplay Backlog

### Beginner Mode

- [x] Collapse secondary sections by default in Beginner Mode.
- [x] Tuck `Accuse` into a collapsed `Final Call` drawer.
- [x] Replace the hero-style marquee with a compact status strip.
- [x] Condense `Decision Desk` and other turn-state copy.
- [x] Tighten panel spacing and reduce editor heights.
- [x] Strengthen pill, helper-note, and footer contrast.
- [x] Add cache-busting for CSS/JS so refreshed screenshots show current frontend code.
- [x] Implement the first Beginner Mode layout pass using the current research anchors.
- [x] Complete the approved initial Beginner Mode redesign plan.
- [ ] Keep trimming remaining low-value copy and vertical bulk.
- [ ] Re-check whether more helper text can still be removed after fresh screenshots.
- [ ] Reduce remaining dead space at zoomed-out desktop sizes.
- [ ] Decide whether `Table Wire` should move or compress further relative to `Caseboard`.
- [ ] Review fresh cache-busted screenshots with special attention to header/pill/footer readability.
- [ ] Do Beginner Mode pass 2 for remaining dead space and final `Table Wire` vs `Caseboard` balance.

### Player Mode

- [x] Complete `docs/CLUE_player_mode_plan.md`.
- [x] Repeat the improvement process with focus on Player Mode.
- [x] Player Mode layout pass from fresh screenshots: briefing above board, desk beside board, wire below board.
- [x] Player board movement fix: movement is primary after rolling, highlighted routes are clickable, and the Caseboard scales down more predictably.

### Superplayer And Administrator Modes

- [ ] Prepare Superplayer Mode, but keep implementation behind the NHP/chatbot priority.
- [ ] Reorganize Superplayer prep materials when Administrator Mode data surfaces are clearer.
- [ ] Design Superplayer Mode as an administrative / Dungeon Master style UX with settings, status displays, saved-game inspection, and NHP/player controls.
- [ ] Preserve future Superplayer hooks in planning and docstrings where relevant.

### Board And Flow

- [x] No more `Moves` destinations at starting locations.
- [x] Make the gameboard scalable.
- [x] Make board movement click-to-move.
- [x] Commit movement on click instead of waiting for the `Commit Move` button.
- [x] Feed scrolls show latest entries at the top.
- [x] Add UI-only `Quit Game` link back to Clue Home.
- [ ] Improve board aesthetics, especially jagged or irregular lines.
- [ ] Add Pause, Save, and Resume game controls after persistence semantics are complete.

## Current Shipped State After v1.8.0

- [x] Standalone Flask app plus AIX-mount-safe routing.
- [x] Deterministic Clue rules engine with filtered seat snapshots.
- [x] Mixed human and autonomous seats under one Game Master.
- [x] SQL-backed persistence for games, seats, tokens, notebooks, events, durable memory, durable relationships, and append-only NHP notes.
- [x] YAML-driven turn/chat profiles and persona-social guidance.
- [x] OpenAI Agents SDK runtime with read tools, durable memory/social write tools, guardrails, local encrypted sessions, and fail-loud LLM turn/chat handling.
- [x] Reactive and proactive NHP chat behavior with bounded cooldown/throttle handling.
- [x] Browser UI with polling synchronization and seat-private/public separation.
- [x] Beginner and Player table UI modes, with Superplayer reserved for later.
- [x] Per-seat UI mode selection for active table seats.
- [x] Fresh setup seeds for new games, so deals and case files are no longer repeated from a fixed default.
- [x] Maintainer documentation updated for the `v1.8.0` chatbot runtime.

## Engineering Backlog

### Test Coverage

- [ ] Add browser/API end-to-end coverage for a full human-only game, including reconnect after refresh or tab reopen.
- [ ] Add a multi-browser regression that proves separate seat tokens always restore the correct private view.
- [ ] Add a small replay/eval harness so prompt or profile changes can be tested against stored traces and expected outcomes.

### Runtime And Gameplay

- [ ] Re-verify fail-loud LLM behavior under live timeout and malformed-output scenarios, not just mocked tests.
- [ ] Continue improving suggestion ranking and opponent-model hooks only if the change preserves the current rules/guardrail boundary.
- [ ] Decide whether an optional planner baseline should be added behind the existing `SeatAgent` interface.

### Deployment

- [ ] Re-test deployed Clue against the shared Cloud SQL backend and then reassess whether the single-instance App Engine cap can be relaxed safely.
- [ ] Re-verify deployed Secret Manager resolution for `OPENAI_API_KEY` after each release.

### Research / Future Exploration

- [ ] Evaluate an optional ISMCTS baseline only after the replay/eval harness is in place.
- [ ] Consider POMCP or related belief-search work only if ISMCTS proves useful enough to justify the complexity.
- [ ] Export clean training traces only after browser E2E coverage and replay evaluation are stable.
- [ ] Consider supervised, DAgger, or population-style experiments only after the non-ML baseline and evaluation loop are trustworthy.

### Documentation And Process Defaults

- [x] Keep README plus `docs/` aligned with the shipped code rather than with an old plan.
- [x] Keep new public modules, classes, and functions maintainer-documented when added.
- [ ] When env defaults or runtime contracts change, update `README.md`, `docs/ClueMLRuntime.md`, and any version-sensitive tests in the same change.
- [ ] After any non-trivial repo change, compose a copy-paste-ready uncompiled Markdown commit summary.
