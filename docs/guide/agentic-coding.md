[← Back to the README](../../README.md) · [Documentation index](index.md)

# Agentic coding — how this project is built with IBM Bob

This project is developed primarily through **agentic coding**: most features are
designed, built, reviewed, and documented by [IBM Bob](https://bob.ibm.com)
(an AI coding assistant) working under a set of written conventions, rather than by
a person typing every change. This page is the map of that setup — the configuration
that steers the agent and the conventions it follows. The detail lives in the linked
documents; this page only orients you.

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

The **Implement** stage uses a lean **Orchestrator** that delegates to a small set
of specialized **modes**. The model is defined in [roles.md](../handoff/roles.md):

- **Orchestrator** (Bob's built-in Orchestrator mode, tool access `None`) —
  dispatches work by delegating to the role modes **one subtask at a time**, owns
  the integration branch and all merges, and is the **sole writer** of the progress
  tracker and shared config (dependencies, CI). It does not do the context-heavy
  work itself.
- **Build mode** — implements a phase's tasks against its durable brief; the only
  mode that edits files.
- **Review / QA / Adversarial modes** — review each diff, run the test gates, and
  then independently try to *break* the result before it ships (each is read-only +
  command; only Build writes).

The role modes live in [`.bob/custom_modes.yaml`](../../.bob/custom_modes.yaml).
Delegation is **sequential** — Bob has no parallel fan-out and no git worktrees
(two named [parity gaps](../handoff/roles.md) versus the project's former harness;
the single serialized rust-analyzer already forced most work one-at-a-time, so the
impact is narrow).

The build advances **one phase per pass** through a fixed sequence of gates, then
stops for a human to look before the next phase. To advance it, switch to the
**Orchestrator** mode and say *"continue the build"* — that activates the
[`continue-build` skill](../../.bob/skills/continue-build/SKILL.md), which carries
the cycle playbook. The human-facing kickoff doc is [continue.md](../handoff/continue.md).

- [adversarial-review.md](../handoff/adversarial-review.md) — the
  contract-falsification gate: an independent pass in a **fresh Bob session** tries
  to make the change violate its contract. Every confirmed break becomes a
  regression test, and it runs after QA on every phase (and as a "gate-zero"
  self-review of the handoff docs before any building starts).
- [progress.md](../handoff/progress.md) — the **single source of truth** for build
  state: a phase table, a dependency graph, and an append-only log. The
  orchestrator is its only writer. (The original Phases 0–5 build is recorded there
  as complete.)
- The per-phase **durable prompts** ([phase-0](../handoff/phase-0-foundation.md) …
  [phase-5](../handoff/phase-5-doc-rag.md)) are self-contained briefs so a phase
  can be resumed from a cold session without the conversation history.

One hard rule shapes all of this: the live rust-analyzer is a **single serialized
resource**. Under sequential delegation it is never contended — only one subtask
drives the analyzer or the integration gate at a time.

## Cross-cutting rules (every stage)

- [research-policy.md](../conventions/research-policy.md) — **Context7-first.**
  Confirm any library/API/SDK detail against [Context7](https://context7.com) and
  current first-party docs before trusting training memory; when the docs are
  silent, read the installed package source.
- [caching.md](../conventions/caching.md) — where confirmed findings land so they
  aren't re-derived: durable reference material goes to
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

## The Bob configuration in this repo

- **[AGENTS.md](../../AGENTS.md)** (repo root) — the instruction spine Bob loads
  every session. It is intentionally **thin**: a pointer index that `@`-imports a
  small always-load core ([agents-core.md](../../agents-core.md)), with the detail
  living in `docs/`. Keeping it thin is itself a convention
  ([agents-md-layout.md](../conventions/agents-md-layout.md)).
- **[`.bob/skills/`](../../.bob/skills/)** — project-local
  [Bob skills](https://bob.ibm.com/docs/ide/features/skills) checked into the repo:
  `grill-me` (drives the grilling interview, bundling its
  [project style](../../.bob/skills/grill-me/project-style.md) in-folder),
  `mcp-builder` (a guide for building MCP servers), and `continue-build` (the
  Orchestrator's build-loop playbook). Bob activates a skill automatically when your
  request matches its `description`.
- **[`.bob/custom_modes.yaml`](../../.bob/custom_modes.yaml)** — the build role modes
  (`build`, `review`, `qa`, `adversarial`), each with its tool `groups` scoped so
  only Build can edit. The lean coordinator is Bob's built-in Orchestrator mode.
- **`.bob/mcp.json`** — a developer's local, **un-committed** MCP server config (e.g.
  pre-approving Context7 lookups). It is per-user and gitignored by choice; the
  expected dev MCP setup is documented in `docs/`, not committed.
- **Memory** — Bob has **no** per-user persistent auto-recall store, so durable
  project knowledge that belongs to everyone goes in `docs/` (see
  [caching.md](../conventions/caching.md)) — already the project convention.

## Where to start

1. Read [AGENTS.md](../../AGENTS.md) — the thin index of everything below.
2. Read the [lifecycle](../conventions/lifecycle.md) — the spine that connects the
   conventions.
3. Open the specific convention for the stage you're in (grill, plan, verify,
   implement, document) when its trigger applies.
4. To advance the build, switch to Orchestrator mode and say *"continue the build."*

The [conventions index](../conventions/index.md) lists every convention in one
place.

## Related pages

- [Development setup](development.md) — the dev container, running the server, and the tests.
- [Architecture](architecture.md) — how the running system fits together.
- [Components](components.md) — a module-by-module tour of the source.
