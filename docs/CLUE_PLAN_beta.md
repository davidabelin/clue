# Clue In-Game UX Overhaul (Implemented Scope)

## Final Scope
- Restrict changes to the in-game seat experience at `/game`.
- Preserve rules, legality, privacy boundaries, and server-side gameplay flow.
- Improve ease of action selection, live reading, chat use, and private note-taking without introducing new backend sync behavior.

## Implemented Changes
- Restored the stable polling-based game API and request flow.
- Redesigned the in-game page into:
  - `Caseboard`
  - `Decision Desk`
  - `Private Briefing`
  - `Table Wire`
  - collapsed `Advanced Diagnostics`
- Preserved in-progress action dropdown selections across refreshes by rebuilding controls only when legal actions actually change.
- Kept board-click movement as a draft helper, not an immediate commit.
- Preserved unsent chat text and notebook edits during polling updates.
- Switched chat and narrative logs to append-only rendering so the feed no longer redraws from scratch on every refresh.
- Added smart chat autoscroll with unread-count behavior when the user is reading older messages.
- Added Enter-to-send chat with Shift+Enter newline support.
- Kept all gameplay, privacy, and autonomous-seat behavior on the existing server path.

## Explicit Non-Changes
- No SSE endpoint.
- No snapshot cursor field.
- No runtime idle-chat trigger config.
- No gameplay rule changes.
- No deployment or repository publishing steps.

## Validation
- `pytest -q tests/test_app.py`
- `pytest -q tests`
