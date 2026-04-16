# Clue Implementation History And Architecture Decisions

This file keeps the historical `CLUE_PLAN_alpha.md` name, but it now serves as a current **v1.7.5** implementation-history record. The original forward plan has been replaced by the decisions that actually shaped the shipped repo.

## What Shipped
- Standalone Flask app plus AIX-mount-safe routing
- Deterministic classic Clue rules for 3 to 6 active seats
- Signed seat-token flow instead of full user accounts
- SQL-backed persistence for current state, notebooks, and append-only events
- Mixed human and autonomous seats under one authoritative Game Master
- Shared deduction substrate used by both heuristic and LLM-backed seats
- OpenAI Agents SDK integration with local encrypted session memory, read-only tools, and guardrails
- Player-facing browser UI with polling synchronization and seat-private/public separation

## Architecture Decisions That Still Matter

### 1. The Game Master owns the game
Reason:
- legality and hidden-state handling needed to stay deterministic, testable, and auditable

Consequence:
- no prompt, tool, or model output may mutate gameplay state directly
- every future autonomous path must still feed back through `TurnDecision` plus `GameMaster.apply_action()`

### 2. Filtered snapshots are the privacy boundary
Reason:
- both the browser and autonomous seats need seat-local views of the same game

Consequence:
- privacy must be enforced server-side before data reaches either the UI or the model-facing runtime
- filtered snapshot changes are high-risk and require careful review

### 3. Keep the browser thin
Reason:
- the same repo needed to run standalone and under AIX without a separate frontend build pipeline

Consequence:
- server remains authoritative
- polling is preferred over a heavier realtime stack in v1
- client code focuses on rendering plus draft-state preservation

### 4. Keep heuristic and LLM seats behind one interface
Reason:
- runtime orchestration should not branch on implementation details
- fallback behavior needed to be immediate and reliable

Consequence:
- `SeatAgent` remains the stable contract
- heuristic play continues to matter even as the LLM path improves

### 5. Local-first LLM memory and secrets
Reason:
- seat-private context, notes, and diagnostics should stay under repo/operator control by default

Consequence:
- local encrypted session storage is the default
- Secret Manager is optional indirection for deployment, not a hard dependency for local development

### 6. YAML-driven persona and model selection
Reason:
- prompts and runtime tuning needed to be maintainable without code edits for every style tweak

Consequence:
- `models.yaml` and `personas.yaml` are part of the public maintainer surface
- the loader degrades gracefully when YAML is missing or malformed

## Decisions Retired Or Reframed
- The original alpha plan was a forward implementation checklist. That role now belongs to `docs/clue_to_do.md`.
- The repo no longer documents itself as an incomplete prototype. The current docs should describe the shipped architecture first, then remaining backlog.
- Historical research-heavy planning material still informs future work, but it is no longer the canonical operational documentation.

## Current Constraints Inherited From The Original Build
- Polling-based sync is simple and robust, but it leaves reconnect and multi-browser E2E testing as important follow-up work.
- JSON state persistence keeps application logic centralized in Python, but schema drift must be managed in code and tests rather than with strict relational constraints.
- The social-memory layer is intentionally bounded and code-normalized; future expansions should preserve that boundedness.
- The LLM path is intentionally conservative. Any future expansion should preserve read-only tools, explicit guardrails, and fail-soft fallback.

## If You Are Revisiting The Architecture
Ask these first:
1. Does the change weaken deterministic gameplay authority?
2. Does the change move privacy assumptions out of code and into prompt text?
3. Does the change make fallback or local development materially worse?
4. Does the change require docs, env-var guidance, and tests to move together?

If the answer to any of those is yes, treat the change as architectural rather than cosmetic.
