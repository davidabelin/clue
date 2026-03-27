# Clue Standalone Web App + AIX Lab Plan

## Summary
- Build `clue` as a standalone Python 3.14 Flask app, shaped like the sibling labs (`clue_core`, `clue_storage`, `clue_agents`, `clue_web`, `tests`, `docs`) and mounted into AIX only after standalone local correctness is established.
- The first playable target includes full classic Clue rules, a dynamic browser UI, and mixed human/LLM seats from the start.
- Core architecture is deterministic: a code-owned Game Master, an append-only event log with visibility scopes (`public`, `seat:<id>`), filtered seat snapshots, and LLM seats that can talk but cannot own rules or state.
- Initial live UX uses structured turn APIs plus short-interval polling for board/chat/event updates; SSE, voice, and richer realtime transport are later phases.
- As of 2026-03-26, the alpha foundation is playable locally, mounted in AIX, and production-ready for Secret Manager-backed LLM credentials plus a shared Cloud SQL backend; remaining open work is tracked in `docs\clue_to_do.md`.

## Key Interfaces / Types
- Core domain types: `GameConfig`, `SeatConfig`, `Card`, `BoardNode`, `TurnState`, `PublicGameEvent`, `PrivateSeatEvent`, `ActionRequest`, `ActionResult`, `SeatSnapshot`.
- Required game actions: `roll`, `move`, `suggest`, `show_refute_card`, `pass_refute`, `accuse`, `send_chat`, with Game Master validation for turn order, movement count, secret passages, private refutation, and elimination-on-wrong-accusation.
- Persistence contract: `games`, `seats`, `events`, `seat_tokens`, plus a cached snapshot/projection layer; SQLite locally, Postgres via `DATABASE_URL` in deployment.
- Browser/API contract: create game, join seat, fetch filtered snapshot, fetch events since cursor, submit action, submit chat, request notebook update; each human seat uses its own signed seat token instead of full accounts in v1.
- LLM contract: one `SeatAgent` interface returning JSON-only `TurnDecision` plus optional `ChatIntent`; v1 uses OpenAI Responses API with structured outputs and local tool functions for deduction/planning, not free-form autonomous control.
- AIX integration contract: later add `load_clue_app()`, `AIX_CLUE_REPO`, a `clue` lab spec, hub card metadata, and a `/clue/*` dispatch route to the standalone service.

## Implementation Progress
- [x] Scaffold the `clue` repo into the sibling-lab shape, add `requirements.txt`, `run.py`, config loading, and a stub `clue_web.create_app()`.
- [x] Define canonical card constants, player/seat models, board graph, dice model, secret passages, and setup/deal logic for 3-6 players.
- [x] Define the authoritative event schema and reducer for setup, rolls, movement, suggestions, refutations, accusations, chat, and private card reveals.
- [x] Implement the Game Master turn arbiter with legality checks, suspect-token movement on suggestion, and “wrong accusation stays in refute loop but cannot win.”
- [x] Write pure-engine tests for setup correctness, movement legality, refutation order, private reveal routing, accusation outcomes, and deterministic replay from events.
- [x] Add SQLAlchemy-backed repositories for games, seats, events, and seat tokens plus replay-backed snapshot builders with public/private filtering.
- [x] Add host game creation, seat invitation/join URLs, and a no-account seat-auth flow based on signed tokens.
- [x] Add JSON endpoints for snapshot fetch, cursor-based event fetch, turn submission, refutation submission, and chat submission.
- [x] Build the first dynamic UI: SVG board with legal-move highlighting, token positions, hand panel, notebook panel, event log, and public round-table chat.
- [x] Add client polling, reconnect handling, pending-action states, and turn/seat banners so multiple browsers stay synchronized without WebSockets.
- [x] Define the `SeatAgent` interface and separate `heuristic` vs `llm` implementations so the web/runtime layer does not care which kind of seat is active.
- [x] Implement the per-seat deduction engine: hard constraints on card ownership, hand sizes, case-file exclusivity, and public/private observation ingestion.
- [x] Implement consistent-deal sampling, envelope marginals, uncertainty scoring, and a simple accusation-risk threshold; this is the first real decision core for LLM seats.
- [x] Expose tool functions (`kb_apply_event`, `sample_consistent_deals`, `rank_suggestions`, `accusation_risk`) and the structured `TurnDecision` schema to the LLM seat runtime.
- [x] Implement prompt assembly that feeds only legal private context to each LLM seat, plus a chat-output validator that blocks hidden-card ownership claims and off-turn rule violations.
- [x] Add background LLM turn execution with a local worker path first and a Cloud-Tasks-ready worker endpoint shape second; enforce move time budgets and safe fallbacks.
- [x] Add standalone deployment files for a `clue` service and environment variables for model key/config, database, and worker settings.
- [x] After standalone local success, add the AIX adapter, lab registry entry, hub metadata, and dispatch routing so `/clue` behaves like the other sibling labs.
- [x] Add AIX smoke tests and docs covering local mount, standalone launch, and the expected env var contract.
- [x] Run mixed-seat integration tests with mocked LLM responses, then one real-model smoke path, until a full four-seat game can finish without illegal actions or privacy leaks.
- [x] Polish the UI for the actual round-table experience: notebook affordances, clear private/public separation, readable reveal prompts, and better pacing for AI turns.
- [x] Wire production `OPENAI_API_KEY` retrieval through Secret Manager while keeping direct local env-var overrides available.
- Remaining open implementation work from the alpha plan now lives in `docs\clue_to_do.md`, including browser/API end-to-end coverage, structured logging and eval hooks, planner upgrades, and later training/export work.

## Test Plan Status
- [x] Engine unit coverage exists for setup invariants, 3-6 player dealing, suggestion side effects, refutation routing, accusation elimination, and private-event visibility filtering.
- [x] API/UI smoke coverage exists for home-page rendering, seat-token snapshot flow, notebook persistence, six-seat `NP` configuration, and mixed-seat mocked completion.
- [x] LLM safety coverage exists for malformed JSON, illegal actions, timeout/no-response, private-information chat leaks, and Secret Manager-backed key resolution.
- [x] Local real-model smoke path completed on 2026-03-26; AIX mount and dispatch smoke coverage also exists.
- Remaining browser end-to-end, reconnect, and manual acceptance work is tracked in `docs\clue_to_do.md`.

## Assumptions / Defaults
- `clue` is the only canonical slug, service name, and repo prefix; no extra naming noise beyond game IDs and timestamps where required.
- The first shippable experience is text-chat round-table plus full board gameplay; voice, realtime streaming, and richer persona fine-tuning are later phases.
- Frontend stays in the existing ecosystem: Flask-rendered pages, vanilla JS modules, SVG/CSS UI, polling-based synchronization, no SPA framework in v1.
- Full classic board rules are in scope from the start, but advanced search/training research is not on the critical path to the first public playable build.
- The plan targets roughly 25 focused work blocks, each sized to fit a single 1-4 hour workday slice.
