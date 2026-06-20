# continue.md — universal dispatcher

Recurring kickoff for Claude Code: **"Continue the build per docs/handoff/continue.md."**
Advances the build by **exactly one phase**, then stops for human review. Re-issue to
advance again.

You are the **orchestrator** (lean — delegate, don't implement). Follow
[roles.md](roles.md) for the agent model and [adversarial-review.md](adversarial-review.md)
for the red-team step.

## The cycle (run once per invocation)

1. **Orient.** Read [CLAUDE.md](../../CLAUDE.md), the
   [implementation plan](../planning/implementation-plan.md), and
   [progress.md](progress.md). Do not relitigate settled decisions.
2. **Gate-zero (own stop point).** If `progress.md`'s gate-zero line is `not-run`,
   launch the adversarial pass over `docs/handoff/` **including this `continue.md`**
   ([adversarial-review.md](adversarial-review.md) → gate-zero). Apply fixes, set
   gate-zero `passed`, then **stop and report** — do not start a phase the same run.
   (If gate-zero is already `passed`, skip to 3.)
3. **Pick the phase.** From [progress.md](progress.md), choose the first phase whose
   dependencies are `done` and is not itself `done`. If two are eligible (e.g. 3+4 and
   5), you may run both **only** if neither drives the live analyzer at the same time
   (5 is analyzer-free, so 3+4's integration gate still serializes).
4. **Handle the Phase 0 seam (branch on current state).**
   - If Phase 0 state is `not-started`/`authoring`: do **Beat A** (host authoring), set
     state `awaiting-container-build`, **stop** and tell the human to build/reopen the
     container.
   - If Phase 0 state is **already `awaiting-container-build`** at entry: the container
     now exists — do **Beat B** (in-container bootstrap + analyzer-path confirmation),
     then continue the cycle.
5. **Plan tasks.** Open the phase's durable prompt. Split into tasks along the
   dependency graph, **partitioned by file ownership**. Mark which are analyzer-free
   (parallelizable) vs analyzer-bound (serial).
6. **Delegate (build).** Launch Sonnet build agents — parallel in their own worktrees
   for analyzer-free tasks; serial for analyzer-bound. You are the **sole writer** of
   `progress.md` and shared config.
7. **Review.** For each returned branch, launch the reviewer agent. Merge clean branches
   into the integration branch. A real conflict → escalate (don't resolve on-thread).
8. **QA.** Launch the QA agent: fast tier always; the live-analyzer integration gate if
   the phase requires it. Fail → bounce to build, re-entering at step 6; after **2**
   rework rounds unresolved, set `blocked` and stop for the human.
9. **Adversarial.** Launch the adversarial agent (intensity per the phase prompt).
   Confirmed breaks become regression tests and bounce to build (re-enter at step 6,
   then re-run review→QA→adversarial); after **2** rework rounds unresolved, set
   `blocked` and stop for the human.
10. **PR + record.** On all-pass, open **one PR for the phase** to `main`. Update
    `progress.md` (state + log line). If the PR can't be opened, set `pr-open`→`blocked`
    with the branch ready and pause for the human.
11. **Stop and report.** Summarize what shipped, gate results, and the next eligible
    phase. **Do not** start the next phase.

## Invariants to enforce every run

- One warm analyzer, serialized; parallelism only on analyzer-free work.
- **Dependency changes (`uv add` / lockfile) are orchestrator-only** — do them serially
  before fanning out; build agents assume deps are already present. Parallel `uv add`
  across worktrees corrupts the shared `pyproject.toml`/lock.
- Confirm the phase's runtime-only `UNVERIFIED` items as you go.
- Stop at the phase boundary; never auto-advance.
