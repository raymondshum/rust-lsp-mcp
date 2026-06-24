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

## How the build actually runs (orchestration)

The **Implement** stage uses a small team of agents with one lean coordinator.
The model is defined in [roles.md](../handoff/roles.md):

- **Orchestrator** (lean) — dispatches work, owns the integration branch and all
  merges, and is the **sole writer** of the progress tracker and shared config
  (dependencies, CI). It does not do the context-heavy work itself.
- **Build agents** — implement tasks, each in its own git worktree on a
  **disjoint set of files**, so merges stay trivial.
- **Reviewer / QA / Adversarial agents** — review each diff, run the test gates,
  and then independently try to *break* the result before it ships.

The build advances **one phase per pass** through a fixed sequence of gates, then
stops for a human to look before the next phase. The project's concrete
dispatcher is [continue.md](../handoff/continue.md); the recurring kickoff is
literally *"Continue the build per docs/handoff/continue.md."*

- [adversarial-review.md](../handoff/adversarial-review.md) — the
  contract-falsification gate: an independent agent, cold context, tries to make
  the change violate its contract. Every confirmed break becomes a regression
  test, and it runs after QA on every phase (and as a "gate-zero" self-review of
  the handoff docs before any building starts).
- [progress.md](../handoff/progress.md) — the **single source of truth** for build
  state: a phase table, a dependency graph, and an append-only log. The
  orchestrator is its only writer. (The original Phases 0–5 build is recorded
  there as complete.)
- The per-phase **durable prompts** ([phase-0](../handoff/phase-0-foundation.md) …
  [phase-5](../handoff/phase-5-doc-rag.md)) are self-contained briefs so a phase
  can be resumed from a cold session without the conversation history.

One hard rule shapes all of this: the live rust-analyzer is a **single serialized
resource**. Agents only fan out on analyzer-free work; anything that drives the
analyzer or the integration gate runs one at a time.

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

Three standing project **constraints** (from [CLAUDE.md](../../CLAUDE.md)) bound
every change: **CI stays light** (lint, type-check, and fast tests only — heavy
integration tests are a local gate), **the host stays clean** (all toolchains
live in the container), and **download once** (heavy artifacts live on persistent
mounts).

## The Claude configuration in this repo

- **[CLAUDE.md](../../CLAUDE.md)** (repo root) — the instructions loaded into every
  session. It is intentionally **thin**: each entry is a pointer plus a one-line
  trigger, with the detail living in `docs/`. Keeping it thin is itself a
  convention ([claude-md-layout.md](../conventions/claude-md-layout.md)).
- **`.claude/skills/`** — project-local [Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills)
  checked into the repo: `grill-me` (drives the grilling interview, reading
  [grill-me.md](../conventions/grill-me.md) for project style) and `mcp-builder`
  (a guide for building MCP servers). Other skills used during development (e.g.
  `code-review`) come from the Claude Code harness, not the repo.
- **`.claude/settings.local.json`** — a developer's local, **un-committed**
  permission allowlist (e.g. pre-approving Context7 lookups to cut prompts).
  Claude Code manages it; you don't need to recreate it. There is no committed
  `settings.json`.
- **Subagents** — there is no `.claude/agents/` directory; orchestration uses
  the generic agents described in [roles.md](../handoff/roles.md), not custom
  definitions.
- **Memory** — Claude Code keeps a small file-based memory **outside this repo** —
  project-scoped, under the user's `~/.claude/projects/<project>/memory/`
  directory, one fact per file. It is local to whoever runs the agent and is never
  committed; durable project knowledge that belongs to everyone goes in `docs/`
  instead (see [caching.md](../conventions/caching.md)).

## Where to start

1. Read [CLAUDE.md](../../CLAUDE.md) — the thin index of everything below.
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
