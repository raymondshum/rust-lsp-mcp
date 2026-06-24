# Bob port — Phase 3 durable brief: Modes + orchestration (the parity core)

Self-contained prompt to execute **Phase 3** of the Bob harness port from a cold
session. Read this + the plan + the tracker, then build. Do not reopen settled
decisions #1–#8 or the Phase 3 design decisions D1–D7 frozen here (grill of
2026-06-24).

## Resume kickoff

> Resume the Bob harness port at **Phase 3**. Read `docs/planning/bob-harness-port.md`,
> `docs/handoff/bob-port-progress.md`, and `docs/handoff/bob-port-phase-3.md`, then
> build Phase 3 — modes + orchestration. Branch `bob_prototype`.

## Where we are

- Phases 0–2 `done` and merged (PR #28 spine, PR #29 skills). Branch `bob_prototype`.
- **Full re-verification of all Bob facts on 2026-06-24** (4-way fan-out vs live
  `bob.ibm.com/docs`): every U-item CONFIRMED except **U5** (skills *are* deliberately
  invokable by request — corrected) and the new **U16** (slash commands). Cache:
  [bob-harness-capabilities.md](../reference/bob-harness-capabilities.md).
- Goal: replace the Claude orchestration model with a Bob one at **behavior parity**,
  naming near-equivalents where Bob can't match.

## Design decisions (frozen — grill 2026-06-24)

### D1 — Dispatcher: built-in Orchestrator mode + `continue-build` skill
Use Bob's **built-in Orchestrator mode** (the *proven* delegator; tool access "None")
as the lean coordinator, with the build-loop playbook in a **`continue-build` skill**
invoked deliberately by "continue the build" (U5-corrected: request-matched
activation). **No `/continue-build` custom mode** — making the dispatcher a custom
mode would gamble the whole cycle on the *undocumented* "can a custom mode delegate?"
question; the built-in Orchestrator's delegation is the one we know works. We
consciously forgo the `/<slug>` slash handle (U16) to protect delegation.

### D2 — Four role modes in `.bob/custom_modes.yaml`
`groups` enforce the write/verify separation — **only Build writes code**:

| slug | name | groups | role |
|---|---|---|---|
| `build` | 🔨 Build | `read, edit, command, mcp` | implements the phase's tasks against its brief; `mcp` for Context7 mid-build; `edit` unrestricted (sequential — no partitioning), with a `roleDefinition` rule *"never write the progress tracker or shared config — orchestrator-only"* |
| `review` | 🔍 Review | `read, command` | reads the diff for correctness/quality; **no `edit`** (reports, doesn't fix) |
| `qa` | ✅ QA | `read, command` | runs the gates (fast tier always; live-analyzer integration gate when the phase requires); **no `edit`** |
| `adversarial` | 😈 Adversarial | `read, command` | falsifies the contract with concrete failing inputs; **no `edit`** (breaks bounce to Build as regression tests) |

Each gets a `whenToUse` tuned for Orchestrator delegation. No `browser`; no `skill`
group on role modes (they execute mechanical work). `fileRegex` is positive-match
only, so Build's "don't touch progress.md" is role discipline, not a hard lock —
parity with the Claude model.

### D3 — `continue-build` skill = generic playbook only (briefs NOT bundled)
Because the Orchestrator (tool access None) **cannot read files itself**, it never
reads the per-phase briefs — it tells the **Build mode** (which has `read`) to
implement "the current phase per its brief in `docs/handoff/`". So the skill holds
only the **generic cycle playbook** (the gate sequence + Orchestrator discipline);
the per-phase briefs **stay in `docs/handoff/`**, read by the role modes. This
sidesteps U6 (no out-of-folder skill files) and keeps the skill tiny. *(Refines
decision #5's "bundling the per-phase briefs" — new info: Orchestrator-None can't
read, role modes can.)*

### D4 — Model-per-role: named parity gap
Bob `custom_modes.yaml` has **no model field** (U11). The Claude model pinned Opus to
orchestrator/adversarial and Sonnet to build/review/QA; Bob can't encode that — the
**human selects the model per mode/subtask in the UI**. Document as a named gap with
guidance: *run Orchestrator and the Adversarial pass on a strong (Opus-class) model;
Build/Review/QA can use a faster one.* Documentation only, no mechanism.

### D5 — Adversarial independence: fresh Bob session (primary)
U9 is re-confirmed **fully silent** (no subtasks page exists) on subtask context
isolation and result-return. So the adversarial gate's independence **cannot** rest on
Bob subtasks: run the adversarial pass in a **fresh Bob session (or `/clear`ed
context) as the primary path**, not a fallback. If a later runtime test shows Bob
subtasks isolate context + return results, relax toward in-session delegation.

### D6 — Doc scope: rewrite the Bob-instance triad; banner the history
- **Rewrite to Bob terms (Phase 3):** `docs/handoff/continue.md`, `roles.md`, and
  `adversarial-review.md` — the project's concrete orchestration *mechanism*.
- **Banner only, later (Phase 4):** the historical per-phase runtime prompts
  (`phase-0-foundation.md` … `phase-5-doc-rag.md`), `progress.md`, the
  implementation-plan — honest Claude-era records.
- **Leave generic:** `docs/conventions/implementation-cycle.md` stays the
  project-agnostic standard (it may describe parallel/worktrees as the general
  superset); the Bob `continue.md` is its **sequential specialization**. Light
  terminology touches only, in Phase 4.

### D7 — Drop the parallel machinery
The Bob `roles.md`/`continue.md` **remove**: git worktrees, parallel fan-out,
file-ownership partitioning (for merge-triviality), the "parallelize analyzer-free
work" rule, and integration-branch cross-merging. They **keep**: the role set, the
gate sequence (**build → review → QA → adversarial → PR**), one-phase-per-pass,
stop-at-boundary, **orchestrator = sole writer of `progress.md`/shared config**,
**dependency changes orchestrator-only/serial-first**, and **rework caps (2 per
gate)**. The single warm analyzer becomes a **one-line note** (everything is serial,
so it is never contended). Integration simplifies to: Build edits the working tree →
Review/QA/Adversarial verify → Orchestrator commits + opens **one PR per phase**.
**Named parity gap: no parallelism** (already in the plan).

## Deliverables

1. **`.bob/custom_modes.yaml`** — the four role modes (D2) with `roleDefinition`,
   `whenToUse`, `groups`.
2. **`.bob/skills/continue-build/SKILL.md`** — the generic cycle playbook (D1/D3):
   Orchestrator discipline + the gate sequence + "delegate to `build`/`review`/`qa`/
   `adversarial` one at a time" + the fresh-session adversarial note (D5) + the
   model-selection guidance (D4). `description` written so "continue the build"
   activates it.
3. **Rewrite** `docs/handoff/continue.md`, `roles.md`, `adversarial-review.md` to Bob
   terms (D6/D7).
4. **Index hygiene** — `docs/handoff/index.md` and any pointer touched.
5. *(Documented fallback, not built unless runtime test #1 fails:*
   `.bob/rules-orchestrator/AGENTS-orchestrator.md` *carrying the Orchestrator
   persona, if the `continue-build` skill turns out not to activate in Orchestrator
   mode.)*

## Definition of done (QA gate)

- Fast tier: YAML schema valid for `custom_modes.yaml` (`customModes:` array; each
  entry has `slug`/`name`/`roleDefinition`/`groups`); markdown lint + link-check; no
  dangling pointers after the rewrites.
- Structural: the four modes' `groups` match D2; the `continue-build` `description`
  covers "continue the build"; the rewritten docs carry no parallel/worktree
  machinery and no stale Claude-mechanism claims.
- **Deferred to the end-of-port Bob-IDE verification** (runtime, can't run mid-port):
  the UNVERIFIED inventory below.

## UNVERIFIED inventory — Phase 3 runtime tests (for the end-of-port Bob-IDE pass)

1. **Skill-in-Orchestrator (load-bearing for D1/D3):** does a skill *activate and
   remain usable* inside the built-in Orchestrator mode (tool access None)? If not →
   move the playbook to `.bob/rules-orchestrator/` (deliverable 5).
2. **Custom-mode delegation:** can a custom mode spawn subtasks into other modes, or
   is delegation Orchestrator-only? (Why we kept the dispatcher on built-in
   Orchestrator — confirm the assumption.)
3. **Subtask isolation + result-return (U9):** do subtasks get cold context and pass
   results back? Drives whether D5's fresh-session rule can relax.
4. **Orchestrator-None read ability:** can the Orchestrator read anything itself, or
   only delegate? (Confirms D3's "role modes read the briefs" split.)
5. **`.bob/rules-{mode}` for Orchestrator:** is `.bob/rules-orchestrator/` read for
   the built-in Orchestrator? (Viability of the deliverable-5 fallback.)

## Adversarial intensity: full

This is the parity core. Red-team the rewritten dispatcher logic the way gate-zero
did the Claude one: can an agent skip a gate, exceed phase scope, write `progress.md`
off the orchestrator thread, or auto-advance past the boundary — *in Bob terms*?

## Do NOT

- Reopen settled decisions #1–#8 or D1–D7.
- Build `.bob/mcp.json` (decision #7 — per-user, gitignored; not Phase 3).
- Run `/init` against the live repo (undocumented overwrite — U3).

## Pointers

- Plan: [bob-harness-port.md](../planning/bob-harness-port.md)
- Tracker: [bob-port-progress.md](bob-port-progress.md)
- Verified Bob facts: [bob-harness-capabilities.md](../reference/bob-harness-capabilities.md)
- Sources to rewrite: [continue.md](continue.md), [roles.md](roles.md), [adversarial-review.md](adversarial-review.md)
