# Implementation cycle (the standard for building a phasal plan)

The standard way a [phasal plan](phasal-plan.md) is built: an **orchestrator**
advances the plan **one phase per pass** through a fixed sequence of gates, then
stops for human review. This is the project-agnostic standard;
[docs/handoff/continue.md](../handoff/continue.md) is this project's concrete
instance and adds its own specifics.

This is the **Implement** stage of the [delivery lifecycle](lifecycle.md).

## Roles

- **Orchestrator (lean).** Dispatches work, owns the integration branch and all
  merges, owns the PR/pause decision, and is the **sole writer** of the progress
  tracker and shared config — including **all dependency/lockfile changes**, done
  serially *before* any fan-out (parallel dependency edits corrupt the shared
  lockfile). It does not do the context-heavy work itself (no implementing, no
  diff-reading marathons, no resolving real merge conflicts on-thread).
- **Build agents.** Implement tasks, each in its **own worktree** on a **disjoint
  set of files** (file-ownership partitioning, so merges are trivial).
- **Reviewer agent.** Reads each branch's diff for correctness and quality before
  merge — keeps review out of the orchestrator's context.
- **QA agent.** Runs the gates: the fast tier always; the heavier gate (live
  services, integration tests) where the phase requires it.
- **Adversarial agent.** After QA passes, independently tries to **falsify the
  phase's contract**. See [adversarial-review.md](../handoff/adversarial-review.md).
  Put a strong model on the risk core.

## The cycle (run once per pass)

1. **Orient.** Read the project instructions, the plan, and the progress tracker.
   Do not relitigate settled decisions.
2. **Gate-zero (own stop point).** Before the first build of a plan, run an
   adversarial self-review over the plan/handoff docs themselves (can an agent
   skip a gate, write shared state concurrently, exceed a phase's scope, merge a
   real conflict on the lean thread?). Apply fixes and re-check until clean; **only
   when clean** mark gate-zero `passed`, then **stop for human review** — do not
   start a phase the same pass. If a finding cannot be resolved, mark gate-zero
   `blocked: <reason>` and stop for the human.
3. **Pick the phase.** Choose the first phase whose dependencies are `done` and
   that is not itself `done`. A `blocked` phase is re-picked and resumed at the
   gate that blocked it once the human has cleared the blocker. Two eligible
   phases may run together **only** if neither contends on the same serialized
   resource **and** their file-ownership partitions are disjoint.
4. **Plan tasks.** Split the phase along its dependency graph, **partitioned by
   file ownership**. Mark which tasks are independent (parallelizable) vs. which
   contend on a shared serialized resource (must run serially).
5. **Build.** Launch build agents — parallel in their own worktrees for the
   independent tasks; serial for the resource-bound ones. Apply any
   dependency/lockfile changes yourself first.
6. **Review.** For each returned branch, launch the reviewer. Merge clean branches
   into the integration branch. A *real* semantic conflict means the partition was
   wrong → **escalate** (fresh agent or human); do not resolve it on-thread.
7. **QA.** Run the fast tier always; the heavier gate when the phase requires it.
   On failure, revert the offending merge and bounce back to **build**.
8. **Adversarial.** Independently attempt to break the phase's contract. Each
   confirmed break becomes a **regression test** and bounces back to build.
9. **PR + record.** On all-pass, open **one PR for the phase**. Update the
   progress tracker (state + a log line). If the PR can't be opened, mark the
   phase `blocked` with the branch ready and pause for the human.
10. **Stop and report.** Summarize what shipped, the gate results, and the next
    eligible phase. **Do not** start the next phase.

## Invariants

- **Shared serialized resources run serially.** Live servers, indexes, warm
  caches, and integration gates are single resources — only one task drives one at
  a time. Parallelism is **only** for independent, file-disjoint work.
- **Shared state is orchestrator-only.** The progress tracker, shared config, and
  dependency/lockfile changes are written by the orchestrator alone, serially,
  before fan-out.
- **Rework caps are per gate: 2 rounds each.** QA bounces and adversarial bounces
  keep separate counters. If a phase can't clear a gate within its rounds, mark it
  `blocked` and stop — no unbounded review→QA→adversarial ping-pong.
- **Confirm the phase's runtime `UNVERIFIED` items** as you build them.
- **Stop at the phase boundary; never auto-advance.**

## Related

- [phasal-plan.md](phasal-plan.md) — the input this cycle consumes.
- [adversarial-review.md](../handoff/adversarial-review.md) — the falsification gate.
- [lifecycle.md](lifecycle.md) — where this stage sits in the whole.
- [docs/handoff/continue.md](../handoff/continue.md) — this project's concrete instance.
