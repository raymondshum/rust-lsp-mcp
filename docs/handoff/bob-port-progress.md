# Bob harness port — progress tracker

Single source of truth for the `bob_prototype` harness-port build state. The
orchestrator is the **sole writer**. Plan: [bob-harness-port.md](../planning/bob-harness-port.md).
This is a **separate** tracker from the historical [progress.md](progress.md)
(original Phases 0–5), which is preserved as a Claude-era record.

## State vocabulary

`not-started → in-progress → qa → adversarial → pr-open → done`, plus
`blocked: <reason>`.

## Gate-zero

This plan + its handoff briefs must pass a cold-context self-review **before**
Phase 1 starts. Phase 0 (verification pass) *is* the gate that retires the
Bob-fact risk; treat unconfirmed `UNVERIFIED` items as blockers.

- Gate-zero: `passed` (2026-06-23 — Phase 0 retired the Bob-fact risk; corrections folded into the plan)

## Phase table

| Phase | Depends on | Parallelizable | State |
|-------|-----------|----------------|-------|
| 0 — Verification pass | — | no | done |
| 1 — `AGENTS.md` spine | 0 | no | in-progress (built; automated QA green; manual Bob-IDE smoke + PR pending) |
| 2 — Skills port | 0 | no | not-started |
| 3 — Modes + orchestration | 0,1,2 | no | not-started |
| 4 — Prose rewrite + branding | 1,2,3 | no (single-track) | not-started |
| 5 — Retire Claude scaffolding | 1,2,3,4 | no | not-started |

## Durable per-phase briefs

- Phase 1 — [bob-port-phase-1.md](bob-port-phase-1.md) (`AGENTS.md` spine)

## Dependency graph

```
0 ──┬─▶ 1 ──┐
    ├─▶ 2 ──┼─▶ 3 ──▶ 4 ──▶ 5
    └───────┘
```

## Log (append-only)

- 2026-06-23 — Plan frozen after grill; tracker created. All phases `not-started`.
- 2026-06-23 — Phase 0 verification pass `done`. 15 items confirmed vs live Bob docs; cached to `docs/reference/bob-harness-capabilities.md`. Corrections (U8/U10/U14) + runtime-only residue (U2/U6/U9) folded into plan; gate-zero `passed`. Phase 1 unblocked.
- 2026-06-23 — Phase 1 durable brief authored (`bob-port-phase-1.md`); handed off for a fresh session. Phase 1 still `not-started`.
- 2026-06-23 — Phase 1 **built** (working tree, not yet committed/PR'd). Deliverables: root `AGENTS.md` (thin shell, `@`-imports the must-load core); new `agents-core.md` at repo root (conventions pointer/trigger table + hard constraints — the `@`-imported core); `claude-md-layout.md` → `agents-md-layout.md` (`git mv`, rewritten for `AGENTS.md` + the `@`-import three-tier load model). Pointers updated: root `index.md` (added `AGENTS.md`/`agents-core.md`, reframed `CLAUDE.md` as Claude-era/retiring), `docs/conventions/index.md`, `CLAUDE.md`, `docs/guide/agentic-coding.md`, `bob-port-phase-1.md` (fixed the would-be-dangling layout link). **Design note:** the core lives at the *repo root* (not `docs/`) so file-relative == repo-root-relative == the inlined-at-root context — eliminates the `@`-import relative-path ambiguity and keeps a file-relative link-checker green. **Automated QA green:** link-check clean across all touched files; `@./agents-core.md` import resolves and is not inside a code fence; `grep -rn "claude-md-layout"` leaves only 4 historical/prose mentions (planning table + plan rename text + the brief's own deliverable/DoD lines) — no live/dangling pointers. **Remaining for `done`:** manual Bob-IDE smoke (AGENTS.md loads, import resolves in a live session, a bare pointer is read on trigger — the U2 runtime check) and PR. No repo markdown linter exists, so the "markdown lint" fast tier has no automated gate here.
