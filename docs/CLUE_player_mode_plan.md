# Clue v1.7.3 Player Mode Launch Plan

## Summary
- Treat current Beginner Mode as “mostly done” and preserve its default table-creation and game UI behavior, except for the shared board-click movement improvement.
- Add table UI modes at game creation: `Beginner` default, `Player` live, `Superplayer` visible as a disabled placeholder.
- Build Player Mode by stripping the current Beginner surface down to play-critical information, chat, and concise suggestion/accusation history.
- Defer real Superplayer work until Player Mode is usable and verified.

## Key Changes
- Add `ui_mode` to create-game flow:
  - `beginner` is the default when omitted and matches current behavior.
  - `player` is selectable and persisted in game `config` and `state`, then exposed in filtered snapshots.
  - `superplayer` appears on the create-table page as “coming later” and is not submitted; direct API attempts should return `400` until implemented.
  - Existing games without `ui_mode` render as `beginner`.

- Keep Beginner stable:
  - Do not redesign Beginner panels in this tranche.
  - Keep current collapsible sections, diagnostics, helper copy, draft preservation, polling, and event rendering.
  - Update only text that would otherwise become false after auto-move clicks.

- Add all-mode board click movement:
  - Clicking or keyboard-activating a highlighted legal board destination immediately submits `{ action: "move", target_node }`.
  - Keep the existing movement select/button in Beginner as a fallback, but Player Mode hides the extra movement controls.
  - Disable board movement clicks while a request is pending; show the existing request error surface if the server rejects the action.

- Implement Player Mode as a lean `/game` variant:
  - Apply a mode class/data attribute from `snapshot.ui_mode`.
  - Hide/remove in Player: marker grid, seat snapshot, suspect lineup, advanced diagnostics, AI explainer, turn rail, most helper notes, movement staging copy, phase/legal-action metrics, and decorative panel labels.
  - Keep in Player: board, current action controls, hand, compact notebook access, public chat composer/feed, private reveals, and a concise play log.
  - Shrink panels and reduce vertical spacing with `.game-app--player` CSS overrides rather than changing Beginner’s current layout.

- Restrict Player narrative:
  - Public play log includes only `suggestion_made`, `suggestion_refuted`, `suggestion_unanswered`, `accusation_made`, `accusation_wrong`, and `accusation_correct`.
  - Exclude movement, dice/rolls, turn starts/ends, game-created messages, suspect-token movement, trace events, and ordinary chat from Player narrative.
  - Keep chat in the chat feed and seat-private card reveals in private intel.

## Public Interfaces
- `POST /api/v1/games` accepts optional `ui_mode`.
- Filtered snapshots include `ui_mode`.
- Valid live modes for this release are `beginner` and `player`.
- No database migration is required because mode is stored in existing JSON config/state payloads.
- No gameplay-rule, privacy, seat-token, or storage schema changes beyond carrying `ui_mode`.

## Test Plan
- Add app/API tests for:
  - home page shows Beginner default, Player selectable, Superplayer disabled placeholder;
  - create-game defaults missing `ui_mode` to `beginner`;
  - create-game with `ui_mode: "player"` returns snapshots with `ui_mode == "player"`;
  - direct invalid or unavailable mode payloads return `400`.
- Keep existing engine and app tests passing, especially legal movement, snapshot privacy, chat, notebook, and autonomous-seat behavior.
- Add targeted tests where practical for mode fallback on older state without `ui_mode`.
- Manual browser smoke:
  - Beginner table still looks like the current table.
  - Player table hides the stripped sections and shows only lean play/chat/private surfaces.
  - Clicking a highlighted board destination commits movement without using `Commit Move`.
  - Player narrative omits movement/turn chatter but keeps suggestions, accusations, and refutation outcomes.

## Assumptions
- This release should be `v1.7.3`; `v1.8.0` remains reserved for the larger multimode milestone.
- Superplayer is only a visible placeholder now, not a selectable live mode.
- Player Mode should err on removing too much UI rather than preserving borderline-helpful Beginner copy.
- The existing Python test suite remains the main automated coverage; fuller browser E2E stays a backlog item unless a JS/browser harness is added later.
