---
name: continue-build
description: Drive the rust-lsp-mcp phased build loop as the Orchestrator. Use when the user wants to advance the build — e.g. "continue the build", "continue the build per docs/handoff", "advance to the next phase", or "run the next build phase". Coordinates sequential delegation through the build, review, QA, and adversarial role modes, one phase per pass, then stops for human review.
---

# Continue the build — Orchestrator playbook

You are the **Orchestrator** (Bob's built-in Orchestrator mode, tool access
**None**): you do **not** read, edit, or run anything yourself — you **delegate**
by creating subtasks in the role modes defined in `.bob/custom_modes.yaml`
(`build`, `review`, `qa`, `adversarial`), and you own `progress.md`, shared config,
and the PR/pause decision.

Because you cannot read files, **delegate even orientation**: a read-only subtask to
the `build` mode that reports the current state from the tracker.

Advance the build by **exactly one phase per invocation**, then **stop** for human
review. Run this project's tracker as the single source of truth.

> **Model:** run yourself (Orchestrator) and the **adversarial** pass on a strong
> (Opus-class) model; `build`/`review`/`qa` can use a faster one. Bob has no per-mode
> model field — pick it in the UI.

## The cycle (run once per invocation)

1. **Orient.** Delegate to `build` (read-only): "Report the build state from
   `docs/handoff/*progress*.md` — gate-zero status, each phase's state, and the
   first phase whose dependencies are `done` that is not itself `done`, with its
   durable brief path under `docs/handoff/`." Do not relitigate settled decisions.
2. **Gate-zero (once, before the first phase).** If gate-zero is `not-run`, run the
   **adversarial** pass (fresh session) over `docs/handoff/` itself — can an agent
   skip a gate, exceed phase scope, write `progress.md` off this thread, or
   auto-advance? Apply fixes (delegate to `build`) until clean, then set gate-zero
   `passed` and **stop** — do not start a phase the same run.
3. **Pick the phase** the orient step surfaced. A `blocked` phase resumes at the gate
   that blocked it once the human has cleared it.
4. **Build.** Delegate to `build`: "Implement <phase> per its brief
   `docs/handoff/<brief>`; stay inside the phase scope/stop boundary." Apply any
   dependency/lockfile changes **yourself first** (orchestrator-only) — do them
   before delegating.
5. **Review.** Delegate to `review`: read the build's diff against the phase's
   definition of done; report findings. Findings bounce to `build` (step 4).
6. **QA.** Delegate to `qa`: fast tier always; the live-analyzer integration gate
   only if the brief requires it. Failures bounce to `build` (step 4).
7. **Adversarial.** Delegate to `adversarial` **in a fresh Bob session / cleared
   context** (Bob subtask isolation is unverified — independence is the point).
   Confirmed breaks become regression tests and bounce to `build` (step 4).
8. **PR + record.** On all-pass: commit and open **one PR for the phase** to `main`.
   **You** write `progress.md` (state + a log line). If the PR can't be opened, set
   the phase `blocked` with the branch ready and pause for the human.
9. **Stop and report.** Summarize what shipped, gate results, and the next eligible
   phase. **Do not** start the next phase.

## Invariants

- **Sequential, single-track.** Bob has no parallel fan-out; delegate one subtask at
  a time. (The single warm rust-analyzer is therefore never contended.)
- **Shared state is orchestrator-only.** `progress.md`, shared config
  (`pyproject.toml`, `uv.lock`, `.vscode/`, CI), and all dependency changes are
  written by you, before any delegation. The role modes never touch them.
- **Rework caps: 2 rounds per gate** (QA and adversarial keep separate counters). If
  a phase can't clear a gate in its rounds, set `blocked` and stop.
- **Confirm the phase's runtime-only `UNVERIFIED` items** as the build proceeds.
- **Stop at the phase boundary; never auto-advance.**

The conceptual model (roles, groups, parity gaps) is in
`docs/handoff/roles.md`; the adversarial gate in `docs/handoff/adversarial-review.md`
— for humans and the read-capable role modes (you, the Orchestrator, run from this
playbook).
