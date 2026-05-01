# Clue TO DO List

- As of 2026-05-01
- Current version: 1.9.0
- Next target: v1.9.1 same-day stabilization, then v2.0.0 release candidate in about two weeks

## Rolling items

**This list stays at the top for loose hints and breadcrumbs. Move items into the structured lists below as soon as they become actionable.**

No rolling items are currently loose. The Clue OpenAI key work is promoted into the v1.9.1 target, and the local-game observations are parked for later triage.

## Later Triage Notes

**Parked until the OpenAI key isolation priority is finished. Keep the pasted debug material only until it has been used to define a concrete follow-up issue.**

- [ ] Temporary malformed-chat JSON breadcrumb from the local game at `http://127.0.0.1:5002/admin/games/clue_20260429204357399738`:

  - Trippy Spinster's LLM chat was unavailable and no heuristic chat was posted.
		{
		  "debug": {
			"tool_writes": []
		  },
		  "error": "Invalid JSON when parsing {\"speak\":true,\"intent\":\"tease\",\"target_seat_id\":\"seat_mustard\",\"topic\":\"Mustard\u0027s theatrical opening suggestion\",\"tone\":\"wry\",\"thread_action\":\"open\",\"relationship_deltas\":[{\"seat_id for TypeAdapter(ChatIntentOutput); 1 validation error for ChatIntentOutput\n  Invalid JSON: EOF while parsing a string at line 1 column 181 [type=json_invalid, input_value=\u0027{\"speak\":true,\"intent\":\"...ship_deltas\":[{\"seat_id\u0027, input_type=str]\n    For further information visit https://errors.pydantic.dev/2.12/v/json_invalid",
		  "mode": "chat",
		  "reason": "model_error",
		  "runtime": {
			"default_model": "gpt-5.4",
			"eval_export_enabled": false,
			"max_tool_calls": 12,
			"max_turns": 18,
			"reasoning_effort": "high",
			"release_label": "v1.9.0",
			"sdk_available": true,
			"sdk_backend": "openai_agents_sdk",
			"session_store": "local_encrypted_sqlalchemy_sqlite",
			"session_ttl_seconds": 900,
			"timeout_seconds": 42.0,
			"trace_include_sensitive_data": false,
			"tracing_enabled": false,
			"version": "1.9.0"
		  },
		  "seat_id": "seat_peacock",
		  "seat_kind": "llm"
		}

  - Trippy Spinster's LLM chat was unavailable and no heuristic chat was posted.
		 {
		  "debug": {
			"tool_writes": []
		  },
		  "error": "Invalid JSON when parsing {\"speak\":false,\"intent\":\"meta_observe\",\"target_seat_id\":\"\",\"topic for TypeAdapter(ChatIntentOutput); 1 validation error for ChatIntentOutput\n  Invalid JSON: EOF while parsing a string at line 1 column 65 [type=json_invalid, input_value=\u0027{\"speak\":false,\"intent\":...rget_seat_id\":\"\",\"topic\u0027, input_type=str]\n    For further information visit https://errors.pydantic.dev/2.12/v/json_invalid",
		  "mode": "chat",
		  "reason": "model_error",
		  "runtime": {
			"default_model": "gpt-5.4",
			"eval_export_enabled": false,
			"max_tool_calls": 12,
			"max_turns": 18,
			"reasoning_effort": "high",
			"release_label": "v1.9.0",
			"sdk_available": true,
			"sdk_backend": "openai_agents_sdk",
			"session_store": "local_encrypted_sqlalchemy_sqlite",
			"session_ttl_seconds": 900,
			"timeout_seconds": 42.0,
			"trace_include_sensitive_data": false,
			"tracing_enabled": false,
			"version": "1.9.0"
		  },
		  "seat_id": "seat_peacock",
		  "seat_kind": "llm"
		}
  - How much of *everything* like this that is saved and recorded along with it used to set the state for next game's Players?
  - Remove this debug material after using it to track down and properly define the problem.

  - I will play another local game, and beforehand I want to set some NHP settings. Where do I edit those?  Would any particular settings be diagnostically useful, ie. for tracking down time-out and other lag problems.

- [ ] Gameplay, UI, probability, and persona questions to re-organize and answer in context:

  - There is still too much temporal "jaggedness" to gameplay flow; some of it waiting for NHP's and for other reasons too. We need to optimize asynchronous game flow or this game will not be playable, so **nothing else matters if we don't figure that out** first.

  - There is still too much space being taken up with panel padding, poor panel adjustment, some extra-large fontsizes, and just bad design of the spatial layout.

  - How are the *probabilities for best next moves* calculated, exactly, and how (optimally) served to the NHP's when they need it? Which NHP parameters affect how they use those likelihoods?

  - "Still in the case," what kind of English is this? Let's have "On the case" like in American 50s Noir.

  - No anachronistic 'Victorianisms'; at best there should be anachronistic Edwardianisms but aren't NHP's supposed to be speaking in Agathie Christienisms?
    - *I don't like it.* The NHPs should be ironic about their in-game role-playing, ie. NOT take it too seriously.
      - Like a group of modern partygoers who have all agreed to play their roles ironically conscious of *playing* them.
    - Where can we define this stage-direction so all the partygoers know about it?


## v1.9.0 Stabilization Pass

Goal: make the existing v1.8.0 chatbot/admin/gameplay surfaces easier to operate, clearer when the live LLM path fails, less laggy during polling, and visually tighter without introducing a major new feature.

- [x] Admin token discoverability: explain on `/admin` that local tokens come from `CLUE_ADMIN_TOKEN`, deployed tokens can come from `CLUE_ADMIN_TOKEN_SECRET`, and production uses the `clue-admin-token` Secret Manager value.
- [x] LLM failure visibility: keep the public `llm_unavailable` gameplay update concise for all seats, and expand Beginner diagnostics with failure reason, affected seat, latency, fallback status, and private trace detail only for the affected seat.
- [x] Lag/performance pass: add browser fetch/render timing and skip unchanged board, seat, event, diagnostics, and explainer redraws during polling.
- [x] Beginner UI polish: trim duplicate helper copy, slightly rebalance Caseboard/Table Wire proportions, and improve low-risk SVG board line rendering.
- [x] Regression coverage: prove admin token guidance does not expose the token and LLM failure debug remains private while public failure events stay visible.
- [x] Release docs and markers: bump the release label to `v1.9.0`, update the changelog, and keep live-check docs current.

## v1.9.1 Same-Day Stabilization Target

Goal: make one locally hosted game feel understandable, responsive, and compact enough that normal play is possible without confusion caused by lag, unclear loading states, or excessive scrolling.

### OpenAI Key Isolation

- [x] Stop using any shared Zenbot/generic OpenAI key path for Clue runtime calls.
- [x] Resolve Clue OpenAI credentials only from explicit tests, `OPENAI_CLUE_SA_KEY`, or `OPENAI_CLUE_SA_KEY_SECRET_VERSION`.
- [x] Pin Clue model traffic to `OPENAI_CLUE_PROJECT_ID=proj_Lw53USO5NinnThSmUspUs1Kt`.
- [x] Store the Clue service-account key in Secret Manager as `clue-openai-api-key` and point both production and smoke deployment configs at it.
- [x] Keep local `set_clue_env.bat` untracked and use it only for local Clue env setup.
- [x] Verify generic `OPENAI_API_KEY` and generic `openai-api-key` no longer satisfy Clue runtime credential resolution.

### Create-Table Feedback

- [ ] Add an immediate loading state after `Create Table` is clicked: disable the button, show a spinner/progress message, and make it explicit that table creation can take time while NHP/LLM seats initialize.
- [ ] Keep the create-table form state visible while loading so it does not feel like the click failed.
- [ ] Surface create-game errors clearly and restore the button if table creation fails.

### Gameplay Latency And Synchronization

- [ ] Identify where the painful local-game delay is coming from: initial table creation, autonomous turn execution, OpenAI calls, polling delay, DOM rendering, or database writes.
- [ ] Add player-visible "working" states when an autonomous seat is acting so the table does not appear frozen.
- [ ] Tighten polling/snapshot behavior enough that separate seats converge quickly and do not appear to drift onto different versions of the table.
- [ ] Reduce or defer any optional NHP chatter/runtime work that makes core turn flow feel stalled.
- [ ] Treat code-caused smoothness problems as blockers for v1.9.1; only external model/API latency should remain outside our control.

### Beginner Mode Density And Navigation

- [ ] Rework Beginner Mode as a compact play surface: the active turn controls, board, private hand/reveals, and table wire must be reachable without five page lengths of scrolling.
- [ ] Remove or collapse redundant explanatory text, oversized padding, repeated headings, and low-value status copy.
- [ ] Prioritize fit-to-display over decorative spacing: shrink cards, controls, logs, and diagnostics until the primary workflow is easy to scan.
- [ ] Keep diagnostics available but secondary; they should not compete with actual play.
- [ ] Review fresh screenshots at normal desktop size and zoomed-out desktop size before calling the density pass done.

### v1.9.1 Release Gate

- [ ] Do not bump to `v1.9.1` until create-table feedback, gameplay lag, and Beginner density have been verified in an actual local game.
- [ ] Update `VERSION`, `clue_core.version`, README/docs/changelog, and version-sensitive tests only after the local-game experience clears the gate.
- [ ] Add a copy-paste-ready commit summary after implementation.

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
- [ ] Re-verify deployed Secret Manager resolution for `OPENAI_CLUE_SA_KEY_SECRET_VERSION`, `CLUE_ADMIN_TOKEN`, and Flask signing secret after each release.

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
