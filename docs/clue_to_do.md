# Clue TO DO List
- As of 4/16/26
- Current version 1.7.3

## *Rolling List*

- [] Superplayer Mode

- [x] Player Mode
  - [x] consult and complete docs\CLUE_player_mode_plan.md

- [] Important: determinism and not-quite-random behavior.
  - [] Am I dealt same hand every time, and what else is the same every time?
  - [] Are calls to random() well-seeded and properly distributed?

- [] When can we have **LLM NPC**s? Because there is nothing but rote nonsense coming from this "placeholder" NPC's. Where is that implementation left hanging, and what can be done?!

- We need some exits from the game:
  - [] Quit Game button (priority)
  - [] Pause, Save, Resume Game buttons...? (not yet a priority)


- [x] No more 'Moves' destinations at starting locations
- [] Gameboard needs big-time redesign
  - [] Make it scalable
  - [x] Make it click-to-move
    - [x] and commit the move upon the click (don't wait for the Commit Move button anymore)
  - [] improved aesthetic
    - [] no jagged, irregular lines
- [x] feed scrolls are all latest-at-the-top

### Ongoing and Immediate UX Improvement Effort
- SCOPE -- we are only speaking right now about *Beginner Mode* (on the new introduction of "Modes", see below)
- [] DECLUTTER -- do a thorough decluttering of player displays
  - [x] Collapse secondary sections by default in Beginner Mode
  - [x] Tuck `Accuse` into a collapsed `Final Call` drawer
  - [] Keep trimming remaining low-value copy and vertical bulk
- [] CONDENSE -- remove "filler" text and leave only what is most relevant to playing the game 
  - be *liberal* with the condensation for Beginner Mode but if *better* information is required or helpful to new players, this is the mode in which it should appear!
	  - ie. err on the side of *declutter and condensation*, and we will add more back to it later if need be
    - *Player Mode* will be even further stripped down to bare essentials, whereas
    - *Super Mode* on the other hand will expand beyond even what is there right now
  - [x] Replace the hero-style marquee with a compact status strip
  - [x] Condense `Decision Desk` and other turn-state copy
  - [] Re-check whether more helper text can still be removed after fresh screenshots
- [] TIGHTEN -- with clutter removed, shrink and excise dead space around what remains
  - [x] Tighten panel spacing and reduce editor heights
  - [x] Strengthen pill, helper-note, and footer contrast
  - [x] Add cache-busting for CSS/JS so refreshed screenshots show current frontend code
  - [] Reduce remaining dead space at zoomed-out desktop sizes
- [] REARRANGE -- *Use legit gameplay UX-design best-practices from reputable sources*
  - [x] Implement the first Beginner Mode layout pass using the current research anchors
  - [] Decide whether `Table Wire` should move or compress further relative to `Caseboard`
- [x] REDESIGN -- create a formal /Plan of specific code changes to make
  - [x] thoroughly study the literature before hard "arrangement" of anything 
  - [x] every choice about placement, visibility, accessibility, etc. should have a sound and justifiable reason for it
- [x] IMPLEMENT -- execute the approved initial Beginner Mode redesign /Plan
- [] VERIFY -- review fresh screenshots after cache-busting and use that evidence for Beginner Mode pass 2
- [x] FINISH -- after /Plan has been approved and implemented, compose a summary of all the changes made in md-format text that I can easily paste into the Commit window in the Source Control panel in VSC

### Tomorrow First
- [] Review fresh cache-busted screenshots with special attention to header/pill/footer readability
- [] Do Beginner Mode pass 2 for remaining dead space and final `Table Wire` vs `Caseboard` balance
- [] Choose the next non-UX priority: `Quit Game`, determinism/randomness investigation, or LLM NPC follow-through

### Planned Shift to Multimodal Gameplay
*Beginner Mode, Player Mode, Super Mode*: A Different UI for each!
  - Right now the game is excluseively in what we will now call *Beginner Mode*
  - *Player Mode* is now live as a stripped-down play surface
  - *Supervisor Mode* (later)
    - will require administrative privileges to access
    - will include knobs for settings, displays of status... essentially a DungeonMaster UX.
    - deferred until other two modes function smoothly and consistently
      - BUT all future planning must keep this mode's eventual implementation in mind (and even noted in docstrings, too)

### Next up on the *rolling list*...
- [] Repeat 'Improvement process' now with the focus on *Player Mode*
- [] And then do a ground-up design and construction of *Super Mode* once
- [] Clue will become version 1.8.0 when **all of the above** is in perfect working order and well-doccumented

## **TO DO Backlog and Follow-Up** (as of **pre-**4/13/26)

### Current State **After** v1.7.3
- [x] Standalone Flask app plus AIX-mount-safe routing
- [x] Deterministic Clue rules engine with filtered seat snapshots
- [x] Mixed human and autonomous seats under one Game Master
- [x] SQL-backed persistence for games, seats, tokens, notebooks, and events
- [x] YAML-driven turn/chat profiles and persona-social guidance
- [x] OpenAI Agents SDK runtime with read-only tools, guardrails, local encrypted sessions, and heuristic fallback
- [x] Browser UI with polling synchronization and seat-private/public separation
- [x] Beginner and Player table UI modes, with Superplayer reserved for later
- [x] Maintainer documentation and targeted docstring sweep completed for `v1.7.3`

### Highest Priority Backlog
- [ ] Add browser/API end-to-end coverage for a full human-only game, including reconnect after refresh or tab reopen.
- [ ] Add a multi-browser regression that proves separate seat tokens always restore the correct private view.
- [ ] Re-test deployed Clue against the shared Cloud SQL backend and then reassess whether the single-instance App Engine cap can be relaxed safely.
- [ ] Re-verify deployed Secret Manager resolution for `OPENAI_API_KEY` after each release.
- [ ] Add a small replay/eval harness so prompt or profile changes can be tested against stored traces and expected outcomes.

### Runtime And Gameplay Follow-Up
- [ ] Re-verify fallback behavior under live timeout and malformed-output scenarios, not just mocked tests.
- [ ] Re-check local and deployed latency budgets for deduction sampling plus LLM turns after future prompt/profile changes.
- [ ] Continue improving suggestion ranking and opponent-model hooks only if the change preserves the current rules/guardrail boundary.
- [ ] Decide whether an optional planner baseline should be added behind the existing `SeatAgent` interface.

### Research / Future Exploration
- [ ] Evaluate an optional ISMCTS baseline only after the replay/eval harness is in place.
- [ ] Consider POMCP or related belief-search work only if ISMCTS proves useful enough to justify the complexity.
- [ ] Export clean training traces only after browser E2E coverage and replay evaluation are stable.
- [ ] Consider supervised, DAgger, or population-style experiments only after the non-ML baseline and evaluation loop are trustworthy.

### Documentation And Process Defaults
- [x] Keep README plus `docs/` aligned with the shipped code rather than with an old plan.
- [x] Keep new public modules, classes, and functions maintainer-documented when added.
- [ ] When env defaults or runtime contracts change, update `README.md`, `docs/ClueMLRuntime.md`, and any version-sensitive tests in the same change.
