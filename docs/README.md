# Clue Docs Map

This directory is maintainer-facing. The documents below are all current for **v1.9.0**, but they answer different questions.

## Start Here
- [`../README.md`](../README.md)
  Canonical repo front door: architecture summary, env vars, local run, tests, and deployment entry points.
- [`ClueDeepDive.md`](./ClueDeepDive.md)
  Full-system walkthrough of how rules, storage, seat agents, social memory, API routes, and browser state fit together.

## Runtime And Operations
- [`ClueMLRuntime.md`](./ClueMLRuntime.md)
  OpenAI seat runtime contract: model/profile selection, tools, guardrails, session memory, tracing, diagnostics, and failure behavior.
- [`CLUE_live_checks.md`](./CLUE_live_checks.md)
  Local, smoke-service, and production read-only verification checklist for the live chatbot path.
- [`CHANGELOG.md`](./CHANGELOG.md)
  Release history for the standalone `clue` repo.

## History And Planning Context
- [`CLUE_PLAN_alpha.md`](./CLUE_PLAN_alpha.md)
  Historical implementation path and the architecture decisions that still shape the repo.
- [`clue_to_do.md`](./clue_to_do.md)
  Current engineering backlog after the `v1.9.0` stabilization release.

## Supporting Material
- [`dont_use.png`](./dont_use_clue_layout.png)
  *Don't use* this screenshot/sketch reference for the then-current table layout and panel vocabulary.

## Which Doc To Update
- Change runtime defaults, tool surfaces, tracing, or session behavior:
  update [`ClueMLRuntime.md`](./ClueMLRuntime.md) and the top-level [`../README.md`](../README.md) if the env contract changed.
- Change the request flow, privacy model, storage model, or overall subsystem boundaries:
  update [`ClueDeepDive.md`](./ClueDeepDive.md).
- Change roadmap priorities or unfinished work:
  update [`clue_to_do.md`](./clue_to_do.md).
- Change release markers or user-visible release framing:
  update [`CHANGELOG.md`](./CHANGELOG.md) and [`../README.md`](../README.md).
