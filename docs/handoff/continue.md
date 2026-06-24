# continue.md — how to advance the build (Bob)

Recurring kickoff: switch to the **Orchestrator** mode in Bob and say
**"continue the build."** That request activates the
[`continue-build` skill](../../.bob/skills/continue-build/SKILL.md), which drives the
build loop **one phase per pass** and then stops for human review. Re-issue to
advance again.

This is rust-lsp-mcp's concrete, **Bob** instance of the general
[implementation cycle](../conventions/implementation-cycle.md) (the project-agnostic
standard). Under Bob it is **sequential, single-track** — no parallel fan-out, no
worktrees (see the parity gaps in [roles.md](roles.md)).

## Where the pieces live

- **The playbook the Orchestrator executes** — the
  [`continue-build` skill](../../.bob/skills/continue-build/SKILL.md). It is
  self-contained because the Orchestrator (tool access `None`) cannot read repo files;
  it delegates everything, including orientation.
- **The orchestration model** (roles, mode `groups`, sequential delegation, parity
  gaps) — [roles.md](roles.md).
- **The role modes** — [`.bob/custom_modes.yaml`](../../.bob/custom_modes.yaml)
  (`build`, `review`, `qa`, `adversarial`).
- **The adversarial gate** (run in a fresh session) — [adversarial-review.md](adversarial-review.md).
- **Build state** (single source of truth) — [progress.md](progress.md).

## What one pass does

Orient → (gate-zero, once) → pick the first phase whose deps are `done` →
**build → review → QA → adversarial** (each a delegated subtask, the adversarial pass
in a **fresh Bob session**) → open **one PR for the phase** → record in
[progress.md](progress.md) → **stop**. The full step list and invariants (rework caps,
orchestrator-only shared state, no auto-advance) are in the
[`continue-build` skill](../../.bob/skills/continue-build/SKILL.md).

## Kickoff prerequisites

- Be in **Orchestrator** mode; run it (and the adversarial pass) on a strong model.
- The role modes in [`.bob/custom_modes.yaml`](../../.bob/custom_modes.yaml) must be
  loaded. If "continue the build" does not activate the skill inside Orchestrator
  mode, that is the runtime-only `skill-in-Orchestrator` unknown — fall back to the
  Orchestrator persona in `.bob/rules-orchestrator/` (see the Phase 3 brief).
