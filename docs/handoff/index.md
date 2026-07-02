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
- [phase-1-docker-verification.md](phase-1-docker-verification.md) — host-side
  checklist + Claude Code prompt to verify the production Docker image (clears the
  repo-agnostic plan's residue R1/R2/R3, which needs a machine with Docker).

## Effort seeds (session handoffs)

Next-session seeds produced by the [session-handoff skill](../../.bob/skills/session-handoff/SKILL.md), which
lives on `main` at `.bob/skills/session-handoff/SKILL.md`:

- [post-sweep-followups-handoff.md](post-sweep-followups-handoff.md) — seed for the post-defect-sweep
  hardening follow-ups: KI-9 (#87, nav-delegate hang), a production-image smoke test (#88), and the Lows
  #89–#92. Tracker: GitHub issues **#87–#92** (label `followup-2026-07-02`); living register
  [../impl/known-issues.md](../impl/known-issues.md) (KI-8, KI-9).
- The 2026-07-01 defect-sweep resolution (issues #45–#63, DS-01…DS-28) is **complete** — evidence in
  [../security/defect-sweep-2026-07-01.md](../security/defect-sweep-2026-07-01.md) (all rows ✅). Its own
  session seed (`defect-sweep-resolution-handoff.md`) lives on the `bob_prototype` branch only.
