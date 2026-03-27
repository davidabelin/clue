# Clue Rolling TO DO List

## Alignment Snapshot
- [x] Review current repo state against `docs\ClueDeepDive.md` and the initial alpha plan.
- [x] Completed DeepDive milestones already in place: correct/testable environment, inference core with consistent-deal sampling, first tool-augmented LLM seat path, and strict public/private routing with leakage guardrails.
- [] Remaining DeepDive milestones on the critical path: formal browser end-to-end coverage, structured tracing/evals, stronger information-gain policy, optional ISMCTS/POMCP planning baseline, and later data export / training loops.

## Done So Far
- [x] Standalone `clue` repo scaffold, Flask app factory, persistence layer, seat tokens, and filtered seat snapshots.
- [x] Classic Clue rules engine for 3-6 seats, event log, private/public visibility routing, and elimination-on-wrong-accusation behavior.
- [x] Browser UI with SVG board, move grid, marker grid, private intel, notebook, and public round-table chat.
- [x] Six-character seat setup with `NP` support and the seed removed from the UI.
- [x] `SeatAgent` split between heuristic and LLM seats.
- [x] Deduction tracker, consistent-deal sampling, marginals, suggestion ranking, and accusation recommendation.
- [x] Structured-output LLM turn selection with legality checks, safe fallback behavior, and public-chat sanitization.
- [x] Mixed-seat mocked completion test coverage, plus a local real-model smoke game completed on 2026-03-26.
- [x] AIX `/clue` integration and standalone deployment files.
- [x] Production Secret Manager wiring for `OPENAI_API_KEY`.
- [x] Production Cloud SQL wiring on `aix-sql` with `clue-database-url` in Secret Manager, so deployed Clue no longer depends on per-instance `/tmp` SQLite.

## Active Engineering Backlog
- [] Re-test deployed Clue with the new shared Cloud SQL backend and then decide whether the temporary single-instance App Engine cap can be removed safely.
- [] Add browser/API end-to-end coverage for a full human-only four-seat game, including dice rolls, movement, room entry, suggestion, refute, accusation, and reconnect.
- [] Add a reconnect and multi-browser regression test that proves separate seat tokens always restore the correct private view after refresh or tab reopen.
- [] Add structured logs and traces around Game Master actions, seat-agent decisions, tool snapshot generation, guardrail blocks, and worker execution.
- [] Persist eval-friendly metrics per turn and per game: illegal-action rejects, turn latency, accusation precision, leakage blocks, fallback rate, and completion rate.
- [] Add a small regression-eval harness so prompt/model changes can be tested against stored game traces and expected outcomes.
- [] Upgrade the current suggestion ranking from marginal-probability scoring to explicit information gain / entropy reduction with opponent-leak penalty, matching the DeepDive recommendation more closely.
- [] Expose richer debug outputs for seat decisions, especially entropy, top hypotheses, accusation confidence, and why a suggestion ranked first.
- [] Add lightweight opponent-model hooks from public refutation and accusation history before attempting larger learning systems.
- [] Profile and cap local/App Engine latency for sampling and LLM turns; record target budgets and enforce timeouts in production.
- [] Verify deployed Clue can read `OPENAI_API_KEY` from Secret Manager end-to-end after each deployment without checking plaintext into source control.

## Planning And Research Next
- [] Add an optional ISMCTS baseline behind the existing `SeatAgent` interface, using the deterministic Game Master plus belief snapshots as the simulator surface.
- [] If ISMCTS is too heavy or not useful enough, evaluate POMCP only after the logging and eval pipeline above is stable.
- [] Export cleaned event traces and private-seat observations into training datasets only after browser E2E and eval instrumentation are solid.
- [] Add supervised / DAgger experiments only after a strong non-ML baseline and replay/eval harness exist.
- [] Later, add population-style evaluation against mixed opponent pools and consider OpenSpiel-based benchmarking once the planner baseline is trustworthy.

## Human UI Testing Checklist

### Setup And Launch
- [X] Start standalone Clue locally and confirm the home page loads without JS errors.
- [] Load Clue through AIX at `/clue` and confirm base-path routing, static assets, and seat links still work.
- [X] Verify the home page shows all six characters, supports `NP`, and does not expose the old seed control.
- [] Create 3-seat, 4-seat, and 6-seat tables and confirm the number of active seat links matches the non-`NP` seats.
  - local
   - [X] creates new game
   - [X] four-seat mixed human/heuristic/LLM (not to completion)
  - clue-dot-aix-labs.uw.r.appspot.com/clue/ 
   - [X] creates new game
  - aix-labs.uw.r.appspot.com/clue/
   - [X] creates new game

### Private State And Sync
- [X] Open at least three seat links in separate browser profiles or incognito windows so seat tokens remain isolated.
- [X] Confirm each seat sees only its own hand, notebook, and private reveal log.
- [X] Confirm public chat and the public event log stay synchronized across all open seats.
- [X] Type unsaved notebook text while polling is active and verify the text is not overwritten before save.
- [X] Save notebook text, refresh the page, and confirm the saved note returns only for that seat.
- [X] Refresh or reopen a seat URL and confirm the correct private snapshot, turn banner, and notebook state are restored.

### Core All-Human Gameplay
- [x] Roll and verify only legal move destinations are highlighted.
- [] Move through hallway nodes, enter a room, and verify position updates across all open seats.
- [] Use a secret passage where available and verify the destination room updates correctly.
- [] Make a suggestion and verify the named suspect token is moved into the room.
- [x] Test a suggestion with a refuter and verify only the suggesting seat sees the shown card in `Private Intel`.
- [] Test a suggestion with no refuter and verify the public log shows the unanswered suggestion with no private card exposure.
- [x] Make a wrong accusation and verify that seat cannot win, gameplay continues, and the table state stays consistent.
- [] Finish a full human-only game and verify winner display, final logs, and stable post-game review.

### Mixed Seats And Safety
- [x] Run a mixed table with at least one heuristic seat and one LLM seat.
- [] Verify autonomous turns resolve within acceptable latency and do not leave the UI stuck between polls.
- [] Confirm AI public chat doesn't state hidden card ownership or other seat-private information.
- [] Confirm fallback behavior is sane if the LLM times out or returns malformed output.

### Deployment And Recording
- [] On deployed Clue, verify LLM seats work with `OPENAI_API_KEY` coming from Secret Manager rather than repo config.
- [] During every manual run, (automatically) record debug info (where?)including the date, seat mix, environment, latency spikes, fallback count, leakage incidents, and any sync defects that still need fixes.

### Miscellaneous Fixes and Improvements
- Local run
  - the Table Record panel:
    - [] needs a scrollbar
    - [] should be latest-first (ie. most recent at top)
  - [] Updates are still constantly over-riding human choices for Move and Accuse drop-downs, resetting to first option in list every ~500ms
- online at aix-labs\clue and clue-aix-labs\clue
  - [x] Create a game (3, 4 players)
  - [] UI choices remain stable (now reverting every ~500ms, making further gameplay impossible)
  - [] (continue checklist online when gameplay possible...)
