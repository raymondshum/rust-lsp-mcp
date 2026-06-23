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
| 1 — `AGENTS.md` spine | 0 | no | not-started |
| 2 — Skills port | 0 | no | not-started |
| 3 — Modes + orchestration | 0,1,2 | no | not-started |
| 4 — Prose rewrite + branding | 1,2,3 | no (single-track) | not-started |
| 5 — Retire Claude scaffolding | 1,2,3,4 | no | not-started |

## Dependency graph

```
0 ──┬─▶ 1 ──┐
    ├─▶ 2 ──┼─▶ 3 ──▶ 4 ──▶ 5
    └───────┘
```

## Log (append-only)

- 2026-06-23 — Plan frozen after grill; tracker created. All phases `not-started`.
- 2026-06-23 — Phase 0 verification pass `done`. 15 items confirmed vs live Bob docs; cached to `docs/reference/bob-harness-capabilities.md`. Corrections (U8/U10/U14) + runtime-only residue (U2/U6/U9) folded into plan; gate-zero `passed`. Phase 1 unblocked.
