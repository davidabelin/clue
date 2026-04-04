# Changelog

## v1.6.1 - 2026-04-03
- Rewrote the repo documentation so the README and `docs/` set now read as current `v1.6.1` maintainer guidance rather than a mix of launch notes and alpha-era planning material.
- Added a docs landing map and refreshed the runtime, architecture, backlog, and implementation-history documents to match the shipped codebase.
- Expanded targeted maintainer-focused docstrings and inline comments in the engine, deduction, runtime, storage, profile-loading, and browser polling/state-preservation hotspots.
- Updated release markers, versioned copy, and tests from `v1.6.0` to `v1.6.1`.

## v1.6.0 - 2026-04-03
- Added richer YAML persona-social schema including signature moves, insecurities, relationship posture, taboos, and per-intent chat examples.
- Added separate deterministic chat profile selection and chat runtime defaults alongside the existing turn-decision model catalog.
- Reworked idle NPC conversation into a two-stage OpenAI Agents SDK chat pipeline with `ChatIntentOutput` and `ChatUtteranceOutput`.
- Expanded code-owned social memory with moods, relationship scores, active chat threads, bounded burst chatter, and refresh-driven cooldown handling.
- Exposed social-summary tools to the SDK so chat and action decisions can use the same seat-safe public context.
- Extended action-side persona influence without changing rule legality, accusation gating, or Game Master authority.
- Updated release markers, docs, diagnostics, and tests for `v1.6.0`.

## v1.5.0 - 2026-03-29
- Added an explicit repo version marker for the standalone Clue lab.
- Reworked the LLM seat runtime around the OpenAI Agents SDK while keeping the deterministic Game Master authoritative.
- Added local encrypted session memory for seat-private LLM context.
- Added read-only Clue tools, output guardrails, and tool guardrails for autonomous seats.
- Expanded diagnostics with model snapshot, reasoning effort, trace id, session id, tool-call summaries, and fallback metadata.
- Updated deployment/runtime configuration for the new seat-agent path.
- Refreshed maintainer documentation for the ML runtime and privacy posture.
