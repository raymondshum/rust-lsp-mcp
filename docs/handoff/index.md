# docs/handoff — Index

How Claude Code executes the plan: durable per-phase prompts, an orchestrator-owned
progress tracker, and a universal dispatcher. Thin pointers — detail lives in the
plan and `docs/reference/`; these files only orchestrate.

- [roles.md](roles.md) — the orchestration model: lean Opus orchestrator + Sonnet
  build / reviewer / QA / adversarial agents; worktrees; parallelism rules.
- [adversarial-review.md](adversarial-review.md) — the contract-falsification pass
  applied after QA on every phase, and gate-zero over this folder.
- [progress.md](progress.md) — **single source of truth for "where are we."** Phase
  states + the dependency graph. Orchestrator is the sole writer.
- [continue.md](continue.md) — the universal dispatcher. Recurring kickoff:
  "Continue per docs/handoff/continue.md."
- Per-phase durable prompts:
  - [phase-0-foundation.md](phase-0-foundation.md)
  - [phase-1-readiness.md](phase-1-readiness.md)
  - [phase-2-resolution.md](phase-2-resolution.md)
  - [phase-3-4-tools.md](phase-3-4-tools.md)
  - [phase-5-doc-rag.md](phase-5-doc-rag.md)
