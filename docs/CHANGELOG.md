# Changelog

## v1.5.0 - 2026-03-29
- Added an explicit repo version marker for the standalone Clue lab.
- Reworked the LLM seat runtime around the OpenAI Agents SDK while keeping the deterministic Game Master authoritative.
- Added local encrypted session memory for seat-private LLM context.
- Added read-only Clue tools, output guardrails, and tool guardrails for autonomous seats.
- Expanded diagnostics with model snapshot, reasoning effort, trace id, session id, tool-call summaries, and fallback metadata.
- Updated deployment/runtime configuration for the new seat-agent path.
- Refreshed maintainer documentation for the ML runtime and privacy posture.
