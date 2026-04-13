# Replacement For `CLUE_FRESH_START_CHECKLIST.md`: Clue v1.7.x UX Renovation Program

## Summary
This effort is a sustained frontend-first UX program for Clue `v1.7.x`, starting today from the current repo state rather than from another blank slate. The goal is to make the in-game `/game` experience feel obviously newer, clearer, calmer, and easier to play, while preserving existing gameplay functionality and stable server behavior.

The work should be guided by current HCI/game-UX research, especially:
- Situation-awareness driven information design and action timing for high-decision interfaces: [Scientific Reports 2024](https://www.nature.com/articles/s41598-024-78043-9)
- Dynamic highlighting under cognitive load: [ETRA / arXiv 2024](https://arxiv.org/abs/2404.14232)
- Interruption recovery cues and workload preservation: [Scientific Reports 2025](https://www.nature.com/articles/s41598-025-09358-4)
- Communication design in online games and trust breakdown risk: [arXiv 2025](https://arxiv.org/abs/2502.17935)
- Information overload in digital tabletop adaptations: [MDPI 2025](https://www.mdpi.com/2076-3417/15/2/715)

Those sources are design anchors, not permission to touch backend/runtime behavior.

## Key Changes
### 1. Starting Assumption For The New Thread
- Begin from the repo as it exists now.
- First audit the current frontend-only changes already present in `game.html`, `clue.css`, and `clue.js`.
- Preserve and refine any existing good interaction improvements already working, especially draft preservation and non-destructive polling behavior.
- Do not restart from scratch unless the audit shows the current frontend work is actively blocking progress.

### 2. Today’s Immediate Objective
- Deliver a clearly visible superficial improvement pass fast enough that a casual viewer can see “new and improved” immediately.
- Keep this first pass confined to:
  - stronger visual identity
  - better hierarchy
  - clearer action prominence
  - cleaner chat presentation
  - better polish and legibility
- Preserve the already-fixed interaction wins:
  - dropdowns do not reset during refresh
  - board clicks only prime moves
  - chat draft survives refresh
  - notebook draft survives refresh

### 3. Today’s Actual Work Plan
1. Audit current in-game UX and list the top friction points still visible in the current frontend.
2. Keep the current stable polling request path and verify no backend/runtime/API/rules changes are needed.
3. Land a fast “surface credibility” pass:
   - stronger first-screen impression
   - more distinctive typography/color/material treatment
   - cleaner button and panel affordances
   - more obvious separation between board, actions, chat, and private intel
4. Immediately follow with the first real UX pass on the same files:
   - action ergonomics
   - chat readability and composer usability
   - information hierarchy and scan speed
   - turn-state comprehension
5. End today by producing:
   - a paste-ready Markdown commit summary
   - a prioritized v1.7.x UX backlog for the next tranche

### 4. v1.7.x Program Structure
- `v1.7.1`:
  - visible redesign foundation
  - action stability
  - chat usability
  - private-briefing clarity
- `v1.7.2`:
  - deeper board/turn-flow ergonomics
  - movement/suggestion/accusation decision scaffolding
  - stronger mobile layout behavior
- `v1.7.3+`:
  - notebook/intel workflow refinement
  - advanced cueing and pacing polish
  - accessibility/readability improvements
  - broader consistency pass across the in-game experience

### 5. Design Doctrine To Follow
- Reduce cognitive overload by cutting competing signals, not by adding more chrome.
- Favor stable spatial grouping so players always know where to look for:
  - what happened
  - what matters now
  - what they can do next
  - what only they can see
- Use highlighting sparingly and intentionally for current legal actions and urgent private prompts.
- Separate low-stakes communication from high-stakes actions so chat never visually competes with committing a move or accusation.
- Preserve attention and working memory during refreshes; live updates must never destroy active user thought.
- Prefer progressive disclosure for diagnostics and secondary detail.

## Public Interfaces / Scope
- No public API, schema, routing, storage, or rules changes are part of this plan today.
- Default file scope:
  - `clue_web/templates/pages/game.html`
  - `clue_web/static/css/clue.css`
  - `clue_web/static/js/clue.js`
- Tests and docs may be updated only after the frontend work is done and only to keep the repo accurate.
- Backend/runtime/API/game-rule files are out of scope unless the user explicitly re-authorizes them.

## Test Plan
- Repo truth check before edits:
  - confirm current game path still uses stable polling
  - confirm no SSE/cursor/runtime rewrite remains in live code path
- Functional regression checks:
  - existing gameplay tests still pass
  - no rules/privacy changes
- UX acceptance checks:
  - dropdown choices remain stable during refresh
  - chat and notebook drafts survive refresh
  - chat feed does not jump while reading history
  - Enter sends, Shift+Enter inserts newline
  - board, action area, and private intel are all readable at a glance on desktop and mobile
- Final validation:
  - run app-level tests
  - run full test suite
  - do a brief manual two-seat smoke check if feasible

## Assumptions And Defaults
- The user wants momentum, not another planning spiral.
- Ask questions only if blocked by a true product decision or if a non-UI change seems unavoidable.
- Today’s bar is:
  - obvious visible improvement fast
  - no new instability
  - clear establishment of the broader v1.7.x UX program
- Do not deploy, do not commit, do not push.
- When the work is done, provide straight Markdown commit-summary text for VS Code Source Control paste.
