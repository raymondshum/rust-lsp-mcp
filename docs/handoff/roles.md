# Orchestration model (roles, worktrees, parallelism)

How the implementation is executed. Read with [progress.md](progress.md) (state) and
[continue.md](continue.md) (the loop).

## Roles

- **Orchestrator — Opus, lean.** Dispatches work, owns the integration branch and all
  merges, owns the PR/pause decision, and is the **sole writer** of
  [progress.md](progress.md) and shared config (`pyproject.toml`, `.vscode/`, CI). It
  does *not* do context-heavy work itself (no implementing, no diff-reading marathons,
  no semantic conflict resolution on-thread). Sole-writer scope includes **all
  dependency/lockfile changes** (`uv add`, `uv lock`): do them serially before fan-out;
  agents assume deps present (parallel `uv add` across worktrees corrupts the shared
  `pyproject.toml`/lock).
- **Build agents — Sonnet.** Implement tasks, each in its **own git worktree** on a
  **disjoint set of files** (file-ownership partitioning, so merges are trivial).
- **Reviewer agent — Sonnet.** Reads each branch's diff for correctness/quality before
  merge. Keeps review out of the orchestrator's context.
- **QA agent — Sonnet.** Runs the gates: the fast tier always; the **live-analyzer
  integration gate** where the phase requires it (the plan's local QA gate, never CI).
- **Adversarial agent.** Tries to *falsify the contract* after QA passes. See
  [adversarial-review.md](adversarial-review.md). Strong model on the risk core.

## Worktrees + the one shared analyzer (critical constraint)

All work runs **inside the devcontainer**. rust-analyzer's cargo `target` / `CARGO_HOME`
and the ChromaDB store live on **shared bind mounts** (download-once). Therefore:

- **The live analyzer is a single, serialized resource.** Only one task at a time may
  drive it or run the integration gate; parallel access contends on / can corrupt the
  shared caches.
- **Parallelize only analyzer-free work** — pure-Python tasks with faked externals (the
  fast-test tier). Anything needing the warm analyzer is serial.
- Worktrees isolate *source edits*, not the caches — never point two concurrent
  analyzer runs at the same target dir.

## Parallelism rule (stated once)

Parallelize **within** a phase, **never across** the risk-first sequence; fan out only
on analyzer-free tasks; partition by file ownership so merges stay trivial. The
per-phase parallel/serial split is encoded in [progress.md](progress.md)'s dependency
graph.

## Merge & conflict policy

Tasks are partitioned to be conflict-free by construction. A *real* semantic conflict
means the partition was wrong — **escalate to a fresh agent or to the human**, do not
resolve it on the lean orchestrator thread.

## Integration & PR

Build agents → worktree branches → orchestrator merges into one **integration branch**
→ after QA + adversarial pass, orchestrator opens **one PR per phase** to `main` for
human review. Opening a PR needs `gh`/GitHub auth; **if it can't open the PR, it pauses
for the human** (with the branch ready). Granularity is per phase, not per task.

## Guardrails (all agents)

Do not relitigate settled decisions (plan's "Settled architecture"). Stay inside the
phase's scope/stop boundary. Confirm that phase's runtime-only `UNVERIFIED` items as you
go. Stop at the boundary and report — never roll into the next phase automatically.
