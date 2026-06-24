# docs/handoff — Index

How the build is executed under the **IBM Bob** harness: an orchestrator-owned
progress tracker, durable per-phase prompts, and a Bob-mode orchestration model. Thin
pointers — detail lives in the plan and `docs/reference/`; these files only orchestrate.

## Live orchestration mechanism (Bob)

- [roles.md](roles.md) — the orchestration model: built-in Orchestrator mode +
  `build` / `review` / `qa` / `adversarial` custom modes; **sequential** delegation
  (no worktrees, no parallelism); named parity gaps.
- [continue.md](continue.md) — how to advance the build: switch to Orchestrator mode
  and say **"continue the build"** (activates the
  [`continue-build` skill](../../.bob/skills/continue-build/SKILL.md)).
- [adversarial-review.md](adversarial-review.md) — the contract-falsification pass
  (the `adversarial` mode, run in a **fresh Bob session**) applied after QA on every
  phase, and gate-zero over this folder.

## Bob harness port (the current effort)

- [bob-port-progress.md](bob-port-progress.md) — **single source of truth** for the
  harness-port build state (separate from the historical [progress.md](progress.md)).
- [bob-port-phase-1.md](bob-port-phase-1.md) — Phase 1 brief (`AGENTS.md` spine, done).
- [bob-port-phase-3.md](bob-port-phase-3.md) — Phase 3 brief (modes + orchestration;
  design D1–D7).

## Historical — the original rust-lsp-mcp runtime build (Claude-era)

_Preserved as honest Claude-era records; era banners land in Phase 4 of the port._

- [progress.md](progress.md) — the original Phases 0–5 build tracker.
- Per-phase durable prompts:
  - [phase-0-foundation.md](phase-0-foundation.md)
  - [phase-1-readiness.md](phase-1-readiness.md)
  - [phase-2-resolution.md](phase-2-resolution.md)
  - [phase-3-4-tools.md](phase-3-4-tools.md)
  - [phase-5-doc-rag.md](phase-5-doc-rag.md)
- [phase-1-docker-verification.md](phase-1-docker-verification.md) — host-side
  checklist + prompt to verify the production Docker image (clears the repo-agnostic
  plan's residue R1/R2/R3, which needs a machine with Docker).
