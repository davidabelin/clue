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
- [x] Add structured logs and traces around Game Master actions, seat-agent decisions, tool snapshot generation, guardrail blocks, and worker execution.
- [x] Persist eval-friendly metrics per turn and per game: illegal-action rejects, turn latency, accusation precision, leakage blocks, fallback rate, and completion rate.
- [] Add a small regression-eval harness so prompt/model changes can be tested against stored game traces and expected outcomes.
- [x] Upgrade the current suggestion ranking from marginal-probability scoring to explicit information gain / entropy reduction with opponent-leak penalty, matching the DeepDive recommendation more closely.
- [x] Expose richer debug outputs for seat decisions, especially entropy, top hypotheses, accusation confidence, and why a suggestion ranked first.
- [x] Add lightweight opponent-model hooks from public refutation and accusation history before attempting larger learning systems.
- [x] Profile and cap local/App Engine latency for sampling and LLM turns; record target budgets and enforce timeouts in production.
- [] Verify deployed Clue can read `OPENAI_API_KEY` from Secret Manager end-to-end after each deployment without checking plaintext into source control.

## Planning And Research Next
- [] Add an optional ISMCTS baseline behind the existing `SeatAgent` interface, using the deterministic Game Master plus belief snapshots as the simulator surface.
- [] If ISMCTS is too heavy or not useful enough, evaluate POMCP only after the logging and eval pipeline above is stable.
- [] Export cleaned event traces and private-seat observations into training datasets only after browser E2E and eval instrumentation are solid.
- [] Add supervised / DAgger experiments only after a strong non-ML baseline and replay/eval harness exist.
- [] Later, add population-style evaluation against mixed opponent pools and consider OpenSpiel-based benchmarking once the planner baseline is trustworthy.

## Documentation
- [x] Thoroughly and meticulously docstring all code
  - aimed at developer/maintainer of the future
    - [x] priority is documenting architecture and functionality
    - [x] keep deeper ML / game-theory context in the design docs, not bloated inline comments
  - [x] python; server-side code
  - [x] html, js, css; front end code
  - [x] .bat files; helper scripts
    - N/A for the standalone `clue` repo itself; AIX-side batch helpers stay documented in the `aix` repo
  - [x] Establish the default that new public modules, classes, and functions should ship with maintainer-focused docs as they are added

## Human/UI Testing Checklist

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
- [] Roll and verify only legal move destinations are highlighted.
- [] Move through hallway nodes, enter a room, and verify position updates across all open seats.
- [] Use a secret passage where available and verify the destination room updates correctly.
- [x] Make a suggestion and verify the named suspect token is moved into the room.
- [] Test a suggestion with a refuter and verify only the suggesting seat sees the shown card in `Private Intel`.
- [] Test a suggestion with no refuter and verify the public log shows the unanswered suggestion with no private card exposure.
- [x] Make a wrong accusation and verify that seat cannot win, gameplay continues, and the table state stays consistent.
- [] Finish a full human-only game and verify winner display, final logs, and stable post-game review.

### Mixed Seats And Safety
- [x] Run a mixed table with at least one heuristic seat and one LLM seat.
- [x] Verify autonomous turns resolve within acceptable latency and do not leave the UI stuck between polls.
- [x] Confirm AI public chat doesn't state hidden card ownership or other seat-private information.
- [] Confirm fallback behavior is sane if the LLM times out or returns malformed output.

### Deployment And Recording
- [x] On deployed Clue, verify LLM seats work with `OPENAI_API_KEY` coming from Secret Manager rather than repo config.
- [x] During every manual run, (automatically) record debug info (where?)including the date, seat mix, environment, latency spikes, fallback count, leakage incidents, and any sync defects that still need fixes.
  - [x] Make this optional: default is NO debugging or log info displayed during gameplay

### Miscellaneous Fixes and Improvements
- **Local** run
  - the Table Record panel:
    - [x] needs a scrollbar
    - [x] should be latest-first (ie. most recent at top)
  - [x] Action dropdown draft-state fix deployed so polling no longer resets Move / Suggest / Accuse / Refute selections to the first option.
  - [] Re-verify **locally** that action choices now remain stable across polling after a full page reload.

- **Online** run
  - at BOTH [x] `aix-labs\clue` and [] `clue-aix-labs\clue`
  - [x] Create a game (3, 4 players)
  - [x] Poll-safe dropdown fix deployed to the live Clue service on 2026-03-26.
  - [] Once selected an option persists, but you have to choose FAST from the drop down -- need *at least* 2000 ms to choose
    - [X] Re-verify online that UI choices now remain stable after a hard refresh / fresh private window.
  - [x] Additional UI panels to provide *friendly but detailed* explanation of how LLM players are integrated into gameplay

- 'Active Seat' display/page panels:
  - [x] Marker Grid: make player summaries to three lines (down fom six) by moving bottom three lines to the right of the upper three
  - [x] reposition "Actions" beneath "Private Seat" where "Table Record" is now
  - [x] reposition "Table Record" beneath "Board Panel" where "Actions" is now
  - [x] remove "Table Seats" (unnecessary with "Marker Grid")
  - [x] put "Seat Debug" and "How Seats Work" together in same panel beneath "Table Record"
  - [x] reposition the Table Record panel to where the Round Table panel is located, and remove Round Table panel.
  - [x] Board panel:
    - [x] Resize it again, or make re-sizeable, to fit just within the boundary of the panel
    - [x] Improve and make more map-like the "navigation lines" wherever there is a one-move connection, so including secret passages, and starting points, without awkward crossings beneath rooms, but more like very lightly stroked "navigation roses" with connecting lines thicker and color-coded for connecting points
      - [x] passageway colors fade from one to the other connecting room-colors
    - [x] Color the rooms individually in pale shades vaguely suggestive of their names
    - [x] Color player markers suggestive of their names
    - [x] Color the starting points with pale shades of the players' colors
    - [x] Remove the Move Grid subpanel
    - [x] Marker Grid subpanel: fix bug causing character names to appear 2x
    - [x] UI Board is click-able to indicate desired move
  - Actions Panel
    - [x] Move the 'Public Table Talk' text window and the 'Send Chat' button to the 'Table Record' panel.

- Gameplay
  - [x] fix LLMs and Heuristics both getting stuck suggesting same 3 answers over and over
  - [x] now fix so non-human players don't Accuse right away
  - [x] remove "trace turn metric" notifications from private intel list
  - [x] implement intelligent heuristic gameplay
  - [x] give stock autonomous seats distinct in-character public table-talk lines for suggestions, accusations, and secret-passage moves
  - [] combine heuristics into all-LLM (or human) players

- Cosmetic
  - [x] /clue page: change text color, can't read "Create a game, open seat links in separate browser tabs, and ..."
  - [x] same footer all pages
