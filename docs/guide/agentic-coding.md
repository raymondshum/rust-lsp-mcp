---
okf_version: "0.1"
type: Guide
title: Agentic coding — how this project is built with Claude Code
description: Maps the delivery lifecycle, orchestration model, and Claude Code configuration that build this project.
tags: [guide, tier-b, agentic-coding]
timestamp: 2026-11-05T00:00:00Z
source_pins:
  - path: CLAUDE.md
    commit: 47012aa930be0efbbb29ac82b4c9e69b949021d3
  - path: docs/conventions/lifecycle.md
    commit: 47012aa930be0efbbb29ac82b4c9e69b949021d3
  - path: docs/conventions/index.md
    commit: 47012aa930be0efbbb29ac82b4c9e69b949021d3
---

[← Back to the README](../../README.md) · [Documentation index](index.md)

# Agentic coding — how this project is built with Claude Code

This project is developed primarily through **agentic coding**: most features are
designed, built, reviewed, and documented by [Claude Code](https://claude.com/claude-code)
(Anthropic's command-line coding agent) working under a set of written
conventions, rather than by a person typing every change. This page is the map of
that setup — the configuration that steers the agent and the conventions it
follows. The detail lives in the linked documents; this page only orients you.

If you are reading the source and wondering "why is there a whole `docs/handoff/`
folder, and what is a *phasal plan*?", start here.

## The delivery lifecycle (the spine)

Every non-trivial change travels the same five-stage path. Each stage hands a
defined artifact to the next, and each has its own convention doc. This is the
**spine** that the rest of the conventions plug into — see
[lifecycle.md](../conventions/lifecycle.md).

```
Grill ──▶ Plan ──▶ Verify ──▶ Implement ──▶ Document
```

| Stage | What it does | Convention |
|-------|--------------|-----------|
| **Grill** | Adversarially interview a rough design until the decisions are settled; leave an `UNVERIFIED` list of claims still to confirm. | [grill-me.md](../conventions/grill-me.md) |
| **Plan** | Turn settled decisions into a **phasal plan** — ordered phases, dependencies, file-ownership partitions, definition-of-done — plus a progress tracker. | [phasal-plan.md](../conventions/phasal-plan.md) |
| **Verify** | Confirm each `UNVERIFIED` claim against authoritative sources before building, so the build doesn't stand on guesses. | [verification-pass.md](../conventions/verification-pass.md) |
| **Implement** | Build the phases one at a time, each through `build → review → QA → adversarial → PR → record`. | [implementation-cycle.md](../conventions/implementation-cycle.md) |
| **Document** | Write the human-facing docs for the shipped behavior, then review and link them. | [documentation-writing.md](../conventions/documentation-writing.md) |

Not every change needs all five stages — a typo fix goes straight to a small
implementation pass. The full lifecycle earns its cost when a change is
substantial, uncertain, or hard to reverse.

## Build orchestration

The **Implement** stage coordinates autonomous agents around an orchestrator (defined in [roles.md](../handoff/roles.md)):

- **Orchestrator**: Dispatches tasks, merges pull requests, and updates [progress.md](../handoff/progress.md).
- **Build agents**: Implement isolated phases in dedicated git worktrees using disjoint file sets.
- **Review and QA agents**: Validate diffs, run test suites, and execute adversarial test passes.

### Phased execution
Builds advance one phase at a time:
1. **Kickoff**: Sessions start by reading [continue.md](../handoff/continue.md).
2. **Phase Briefs**: Each phase uses a standalone durable prompt ([phase-0](../handoff/phase-0-foundation.md) through [phase-5](../handoff/phase-5-doc-rag.md)).
3. **Adversarial Gate**: Independent agents attempt to break acceptance criteria per [adversarial-review.md](../handoff/adversarial-review.md). Confirmed breaks become regression tests.
4. **State Tracking**: [progress.md](../handoff/progress.md) maintains phase status, dependency graphs, and completion logs.

> **Resource constraint**: rust-analyzer processes requests serially. Tasks requiring active language server queries run sequentially rather than in parallel.

## Cross-cutting rules (every stage)

- [research-policy.md](../conventions/research-policy.md) — **Context7-first.**
  Confirm any library/API/SDK detail against [Context7](https://context7.com) and
  current first-party docs before trusting training memory; when the docs are
  silent, read the installed package source.
- [caching.md](../conventions/caching.md) — where confirmed findings land so they
  aren't re-derived: short cross-session facts to memory, reference material to
  [`docs/reference/`](../reference/index.md) (stamped with library + version + date).
- [working-style.md](../conventions/working-style.md) — propose with alternatives
  and a recommendation; prefer the simplest mechanism; capture decisions durably
  in `docs/`, not just in chat.
- [known-issues.md](../impl/known-issues.md) — a living register of open design /
  documentation issues, reviewed at the start of a grill/plan session and at each
  phase's record step, so a discrepancy found once isn't rediscovered later.

Three standing project **constraints** (from [AGENTS.md](../../AGENTS.md)) bound
every change: **CI stays light** (lint, type-check, and fast tests only — heavy
integration tests are a local gate), **the host stays clean** (all toolchains
live in the container), and **download once** (heavy artifacts live on persistent
mounts).

## Claude configuration

- **[AGENTS.md](../../AGENTS.md)** (with [CLAUDE.md](../../CLAUDE.md) pointer): Session entry point. Contains pointers and triggers without implementation details (per [claude-md-layout.md](../conventions/claude-md-layout.md)).
- **`.claude/skills/`**: Checked-in project skills:
  - `grill-me`: Conducts architectural interview sessions.
  - `doc-sweep`: Audits and refreshes `docs/guide/` pages.
  - `mcp-builder`: Guides server creation and tool integration.
  - `rust-code-navigation`: Directs agents to use semantic LSP tools over grep.
  - `session-handoff`: Writes continuity documents under `docs/handoff/`.
- **`.claude/settings.local.json`**: Developer-specific permission allowlist (uncommitted).
- **Subagents**: Uses generic role definitions from [roles.md](../handoff/roles.md) rather than custom agent configs.
- **Memory**: Ephemeral facts reside in `~/.claude/projects/` on the host. Persistent shared project knowledge resides in `docs/` (see [caching.md](../conventions/caching.md)).

## Where to start

1. Read [AGENTS.md](../../AGENTS.md) — the thin index of everything below.
2. Read the [lifecycle](../conventions/lifecycle.md) — the spine that connects the
   conventions.
3. Open the specific convention for the stage you're in (grill, plan, verify,
   implement, document) when its trigger applies.
4. To advance the build, the kickoff is *"Continue the build per
   docs/handoff/continue.md."*

The [conventions index](../conventions/index.md) lists every convention in one
place.

## Related pages

- [Development setup](development.md) — the dev container, running the server, and the tests.
- [Architecture](architecture.md) — how the running system fits together.
- [Components](components.md) — a module-by-module tour of the source.
</content>
