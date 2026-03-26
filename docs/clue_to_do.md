# Clue Rolling TO DO List

- [ ] Final check against groundtruth in docs\ClueDeepDive.md
  - consistent
  - complete

## Initial Implementation Progress
- Cf. docs\CLUE_PLAN_alpha.md

- [x] Prevent polling refresh from resetting in-progress local UI inputs such as notebook text and action selections before the user submits or saves them.

- [ ] Run mixed-seat integration tests with mocked LLM responses, then one real-model smoke path, until a full four-seat game can finish without illegal actions or privacy leaks.
  - Mocked four-seat mixed completion coverage is now in the test suite.
  - Real-model smoke path is still blocked locally because `OPENAI_API_KEY` is not set in this shell.

- [x] Polish the UI for the actual round-table experience: notebook affordances, clear private/public separation, readable reveal prompts, and better pacing for AI turns.

- [ ] Add structured logging and evaluation hooks for illegal action rate, turn latency, accusation precision, leakage blocks, and game completion rate.

- [ ] Later, swap the simple suggestion ranker for ISMCTS/POMCP behind the same `SeatAgent` interface if the heuristic/sampling policy is too weak.
  - [ ] explain to developer what you have in mind here

- [ ] Later, export event logs into training datasets and add supervised/DAgger experiments only after the deterministic engine, UI, and LLM safety path are stable.

- [x] Provide seats for Prof. Plum and Mrs. Peacock for up to *six* possible players (and set the minimum number of players, 3?, as determined by the stated game rules and/or reality).
  - [x] Display all six; include as a drop-down option "NP" (stands for not playing).

- [x] Set seed=17 and remove from UI

## Test Plan

- [x] See clue\docs\flask_output.txt

- [x] Various UI style tweaks: darker, sharper, more ornate; a grid for moves and marker locations

- [ ] Engine unit tests: official deck/setup invariants, 3-6 player dealing, board adjacency, secret passages, suggestion side effects, refutation order, accusation elimination, and deterministic replay.

- [ ] Visibility tests: the suggesting seat sees the shown card; other seats see only the public refuter/pass sequence; public chat never receives private card data.

- [ ] (high priority) API/UI tests: host creates a game, humans join through seat tokens, multiple browsers poll into the same state, and reconnect restores the correct private view.

- [x] LLM safety tests: malformed JSON, illegal actions, timeout/no-response, and private-information chat leaks all downgrade to safe fallback behavior instead of corrupting the game.

- [ ] End-to-end acceptance: one local four-seat mixed game completes start-to-finish; one AIX-mounted smoke game loads through `/clue`; metrics are recorded for latency, leakage count, and completion rate.

## Next step(s)

- [ ] Basic LLM participation
  - [ ] review clue\docs\ClueDeepDive.md for using OpenAI tools to set up LLM participation
  - [ ] present and responsive (even if confused or disoriented)
    - [ ] low lag
  - [ ] use tools correctly and consistently
  
- [ ] Full LLM implementation
  - [ ] referencing clue\docs\ClueDeepDive.md again:
    - [ ] soft training
    - [ ] hard training
