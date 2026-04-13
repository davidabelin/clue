# Clue In-Game UX Overhaul (Gameplay + Multiplayer Chat, Functionality-Preserving)

## Summary
- Current UX blockers (design + interaction): full-screen rerenders on every poll, read-side effects during snapshot refresh, action control rebuilds that interrupt dropdown selection, noisy panel density, and chat/readability friction.
- Scope locked to the in-game seat experience (`/game`) only.
- Visual direction locked to Classic Boardgame, but with gameplay-first hierarchy and collapsed advanced diagnostics.
- Sync strategy locked to SSE with automatic cursor-based polling fallback.
- Idle-chat behavior default remains unchanged for now, but pipeline will be refactored behind a trigger-mode seam so we can switch later without another frontend rewrite.

## Key Changes
- Server sync pipeline:
  - Add `GET /api/v1/games/current/stream` (SSE) emitting seat-filtered snapshot updates keyed by event cursor.
  - Keep existing `GET /api/v1/games/current` and use `since` cursor as fallback transport.
  - Add a snapshot-level cursor field (`event_cursor`) so client can do strict stale-update rejection.
  - Add idle-chat trigger mode plumbing (`read` default), but keep deployed behavior equivalent to today.
- Client state/render architecture:
  - Replace “rerender everything every refresh” with a single state store plus targeted renderers (board, actions, chat, private intel, status).
  - Add a sync manager that prefers SSE, reconnects with backoff, and falls back to incremental polling transparently.
  - Introduce monotonic cursor/version guards so late responses cannot clobber newer UI state.
  - Make writes (`action`, `chat`, `notebook`) flow through one request coordinator to avoid refresh/write races.
- Action UX and real-time choice handling:
  - Stop rebuilding action controls unless legal-action fingerprint actually changes.
  - Preserve dropdown focus/open interaction and draft values across background updates.
  - Keep board-node click-to-move, but tie it to stable draft state and explicit action confirmation.
  - Remove current behavior that clears unsent chat draft after non-chat actions.
- Multiplayer chat UX:
  - Split feed rendering into append-only incremental updates by `event_index` (no full log replacement).
  - Add smart autoscroll: follow bottom when user is at bottom, preserve position when user is reviewing history.
  - Add Enter-to-send with Shift+Enter newline, plus clearer send/disabled/loading states.
  - Keep public/private boundaries unchanged and continue server-side sanitization/guardrails.
- Layout and visual redesign (Classic Boardgame, gameplay-first):
  - Rebuild game page into a decision-first layout: board/action/chat prominence, private intel in a focused side rail.
  - Move Seat Debug and LLM explainer into collapsed “Advanced Diagnostics” by default.
  - Improve mobile structure so board, current legal actions, and chat composer remain usable without panel thrash.
  - Refresh typography, spacing, and contrast to improve scan speed and reduce cognitive load during active turns.

## Public Interface Changes
- New endpoint: `GET /api/v1/games/current/stream` for SSE snapshot updates (token from query/header, cursor-aware).
- Existing endpoint behavior retained: `GET /api/v1/games/current?since=<cursor>` continues to return seat-filtered snapshots.
- Snapshot payload addition: `event_cursor` (highest visible event index represented by the snapshot stream/cursor state).
- Runtime config addition: `CLUE_IDLE_CHAT_TRIGGER` with default `read`; non-default modes are for future activation, not required to preserve current behavior.

## Test Plan
- Backend API tests:
  - Verify SSE endpoint auth, stream content type, and first snapshot payload shape.
  - Verify `since` plus `event_cursor` cursor monotonicity and incremental event behavior.
  - Verify default idle-chat behavior remains unchanged with `CLUE_IDLE_CHAT_TRIGGER=read`.
  - Verify fallback polling path still returns correct seat-private/public filtering.
- Integration regressions:
  - Keep all existing gameplay legality, privacy, and mixed-seat flow tests passing.
  - Update page-content assertions to match new in-game layout labels where needed.
- UX acceptance scenarios:
  - While updates arrive continuously, user can keep a dropdown open long enough to choose without reset.
  - Chat feed does not jump while reading older messages; new-message indication appears when scrolled up.
  - Action submission, notebook save, and chat send remain functionally identical to current rules behavior.
  - SSE disconnect automatically degrades to polling with no loss of turn/state continuity.
- Final validation:
  - Run full test suite (`pytest -q`) and perform manual multiplayer smoke test with at least two seat tabs.

## Assumptions and Defaults
- In-game UX only; create-game landing page remains unchanged this pass.
- No gameplay rules, legality logic, or privacy boundary changes.
- No deployment and no git commit/push in this task.
- Requested skills were honored for planning context; no skill installation or external OpenAI docs lookup is required for this UX-focused local refactor.
- After implementation completes, provide a plain Markdown commit-summary text block suitable for direct paste into VS Code Source Control.
