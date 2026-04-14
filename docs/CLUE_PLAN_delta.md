# Clue v1.7.x Beginner Mode UX Redesign Plan

## Summary
- Redesign only Beginner Mode on `/game`; keep the current polling model, privacy boundary, Game Master authority, and action API flow.
- Replace the oversized marquee with a compact status strip, then declutter and condense the rest of the page so the main action is obvious above the fold.
- Keep the current desktop panel spine unless a clear UX win requires a move, but use moderate collapsible sections to suppress secondary material instead of permanently relocating it.
- Use the research anchors already captured in `docs/CLUE_PLAN_gamma.md`, especially [MDPI 2025](https://www.mdpi.com/2076-3417/15/2/715/html) for reducing digital-tabletop visual clutter and [arXiv 2404.14232](https://arxiv.org/abs/2404.14232) for restrained highlighting under cognitive load.

## Key Changes
- Top hierarchy
  - Replace the current hero-style marquee with a short status bar containing seat name, table title, turn banner, phase, acting seat, turn number, and legal-action count.
  - Remove decorative/filler copy from the header; keep at most one short state sentence when it materially changes what the player should do.
  - Increase contrast and tighten spacing globally so panels read as functional tools instead of decorative cards.

- Decision Desk
  - Compress `Turn Readout` to one primary action sentence plus one helper sentence max.
  - Keep the turn rail, but shorten it to terse step labels and visually subordinate it.
  - Separate consequence levels: keep the next required action open and emphasized, move `Accuse` into a collapsed `Final Call` section by default, and keep `End Turn` visually secondary.
  - Preserve current polling-safe draft behavior for chat, notes, and action selections.

- Board and movement
  - Redraw the board responsively by removing the fixed transform and scaling the SVG from node bounds so it grows and shrinks with viewport width.
  - Refresh the board visual language: clearer room/start/hallway differentiation, stronger legal-target highlighting, stronger selected-target state, and less dead padding around the board.
  - Board clicks stage movement only: clicking a legal destination updates the `move-target` dropdown, previews the move by relocating the current seat token locally, and waits for the existing submit flow.
  - Keep secret-passage targets selectable, but visually distinct from ordinary movement.
  - Fix movement generation so `legal_actions.move_targets` never includes `start` nodes. This is a rules-layer change, not just a frontend hide/filter.

- Private Briefing and Table Wire
  - Keep the right column, but make secondary sections collapsible by default: `Marker Grid`, `Case Notes`, `Suspect Lineup`, and `Advanced Diagnostics` start closed.
  - `Private Intel` starts open only when it contains entries; otherwise it starts closed.
  - Keep `Your Hand`, the compact seat summary, and the main action area always visible.
  - Preserve collapse state locally so polling does not reopen or reclose sections unexpectedly.
  - Change `Witness Record`, `Chat Feed`, and `Private Intel` to newest-first ordering. Prepend new items, keep scroll position stable while reading older entries, and treat “latest” as the top edge instead of the bottom.

## Public Interfaces / Behavior
- No HTTP route, token, storage, or snapshot schema additions.
- Existing action payloads remain unchanged.
- One intentional behavioral contract change: `legal_actions.move_targets` excludes `start` nodes.
- Template markup may change inside the existing `/game` page to support compact status UI and collapsible sections.

## Test Plan
- Engine test: movement legal-actions never expose `start` nodes as destinations.
- Frontend behavior test: board click stages `move-target` and token preview without sending a request; submit still uses the existing action flow.
- Feed checks: `Witness Record`, `Chat Feed`, and `Private Intel` render newest-first, do not jump while reading older entries, and still surface new items correctly.
- Desktop UX checks: no washed-out header, materially less dead space at `100%` and zoomed-out views, board scales with viewport, and `Roll`, `Suggest`, `Refute`, `Await`, and `Game Over` keep the primary action visually dominant.
- Regression checks: chat draft, notebook draft, and dropdown selections survive polling; privacy separation and current polling endpoints remain unchanged.

## Assumptions And Defaults
- This tranche is Beginner Mode only; Player Mode and Super/Supervisor Mode are deferred, but naming and structure should leave room for later mode variants.
- Desktop-first for this pass; mobile-specific redesign is deferred unless needed to avoid regression.
- Moderate disclosure and balanced condensation are the defaults: collapse secondary material, but keep the main play surfaces visible and leave enough helper text for recovery by newer players.
