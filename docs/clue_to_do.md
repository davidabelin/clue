# Clue Rolling TO DO List

- [ ] Final check against groundtruth in docs\ClueDeepDive.md
  - consistent
  - complete

## Initial Implementation Progress
- Cf. docs\CLUE_PLAN_alpha.md

- [x] Prevent polling refresh from resetting in-progress local UI inputs such as notebook text and action selections before the user submits or saves them.

- [ ] Add end-to-end browser/API tests for a full human-only four-seat game, including dice rolls, room entry, suggestion, refute, accusation, and reconnect.

- [ ] Run mixed-seat integration tests with mocked LLM responses, then one real-model smoke path, until a full four-seat game can finish without illegal actions or privacy leaks.

- [ ] Polish the UI for the actual round-table experience: notebook affordances, clear private/public separation, readable reveal prompts, and better pacing for AI turns.

- [ ] Add structured logging and evaluation hooks for illegal action rate, turn latency, accusation precision, leakage blocks, and game completion rate.

- [ ] Later, swap the simple suggestion ranker for ISMCTS/POMCP behind the same `SeatAgent` interface if the heuristic/sampling policy is too weak.

- [ ] Later, export event logs into training datasets and add supervised/DAgger experiments only after the deterministic engine, UI, and LLM safety path are stable.

## Test Plan

- [ ] Engine unit tests: official deck/setup invariants, 3-6 player dealing, board adjacency, secret passages, suggestion side effects, refutation order, accusation elimination, and deterministic replay.

- [ ] Visibility tests: the suggesting seat sees the shown card; other seats see only the public refuter/pass sequence; public chat never receives private card data.

- [ ] API/UI tests: host creates a game, humans join through seat tokens, multiple browsers poll into the same state, and reconnect restores the correct private view.

- [ ] LLM safety tests: malformed JSON, illegal actions, timeout/no-response, and private-information chat leaks all downgrade to safe fallback behavior instead of corrupting the game.

- [ ] End-to-end acceptance: one local four-seat mixed game completes start-to-finish; one AIX-mounted smoke game loads through `/clue`; metrics are recorded for latency, leakage count, and completion rate.
