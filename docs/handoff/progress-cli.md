# Build progress tracker — CLI frontend effort

**Single source of truth for "where are we" on the
[cli-frontend plan](../planning/cli-frontend.md).** The **orchestrator is the
sole writer**; build/reviewer/QA/adversarial agents report results, the
orchestrator records them here. Read by [continue.md](continue.md) (efforts
table) to pick the next phase. The original build's tracker is
[progress.md](progress.md) (complete).

## State vocabulary

`not-started` → `authoring` → `in-progress` → `qa` → `adversarial` →
`pr-open` → `done`. (`blocked` = paused for human, with a one-line reason.
No container seam in this effort — the dev container already exists.)

## Gate-zero (handoff self-review)

`gate-zero: passed (2026-07-03)` — adversarial pass over this effort's handoff artifacts
(the five `cli-phase-*.md` prompts, this tracker, and the continue.md
multi-effort routing) must be `passed` before any phase starts. Values:
`not-run` | `passed` | `blocked: <reason>`. Orchestrator flips it and records
the date in the log below.

## Phase status

| Phase | Prompt | Depends on | Parallelizable? | State |
|-------|--------|-----------|-----------------|-------|
| C1 — Daemon transport | [cli-phase-1-daemon.md](cli-phase-1-daemon.md) | — | With C3 on the fast tier only; podman integration gates serialize | not-started |
| C2 — `rust-lsp` CLI client | [cli-phase-2-cli.md](cli-phase-2-cli.md) | C1, C3 | No (single package build; needs C1's daemon fixture + C3's pinned version fields) | not-started |
| C3 — KI-12 status versions | [cli-phase-3-versions.md](cli-phase-3-versions.md) | — | With C1 on the fast tier only; integration gates serialize | not-started |
| C4 — Deployment + docs | [cli-phase-4-deploy-docs.md](cli-phase-4-deploy-docs.md) | C1, C2 | With C5 (disjoint files) | not-started |
| C5 — Skill revision | [cli-phase-5-skill.md](cli-phase-5-skill.md) | C2 | With C4 (disjoint files) | not-started |

## Dependency graph (what the orchestrator may fan out)

```
C1 ──> C2 ──> C4
C3 ──┘ └────> C5      (C4 ∥ C5 after C2; disjoint files)
                      (C3: no deps; ∥ C1 on the fast tier; integration gate
                       serial; must be done before C2 — it pins the status
                       version fields C2's `version` surfaces)
```

- Cross-phase: strictly the arrows above. Never start a phase whose dependency
  isn't `done`.
- The live analyzer + podman integration gate remain a **single serialized
  resource** across phases and across efforts (see
  [progress.md](progress.md) invariants); parallelism is fast-tier only.
- `uv add`/lockfile changes are orchestrator-only (none expected — the plan
  adds zero dependencies; C2 touches `pyproject.toml` for packaging only).

## Per-phase log (orchestrator appends)

> One line per state transition: `<date> Phase Cn → <state> (PR #/notes)`.

- 2026-07-02 Tracker created with the frozen plan (grill + verification pass +
  2-reviewer adversarial plan review all done same day; see the plan's status
  header). Gate-zero not yet run — first `continue` invocation must red-team
  the five prompts + this tracker + the continue.md routing change before
  starting C1.
- 2026-07-03 Gate-zero → **passed**. Opus red-team over the 8 handoff
  artifacts: verdict `fixable` — 3 must-fixes + 5 minors, all applied:
  (1) C2↔C3 version-field contract pinned (`server_version`,
  `multilspy_version`, `rust_analyzer_version`) + C2 now depends on C3;
  (2) C5 dry-run transcript recorded by the orchestrator, not the QA agent
  (sole-writer rule); (3) automation directive explicitly covers gate-zero's
  stop-and-report; (4) roles.md/adversarial-review.md de-hardcoded from
  progress.md to "active tracker"; (5) continue.md concurrency example gains
  the C1+C3 / C4+C5 pairs; (6) C2's pyproject packaging edit reconciled with
  the shared-config rule; (7) `_server_instances` assertion clarified as
  in-process-only; (8) C1 fixture location/marker/harness pinned. Non-issues
  confirmed: parity exclusions match live tool set; dispatcher still routes
  the original effort correctly. Per the standing automation directive,
  proceeding directly to C1 (∥ C3 fast-tier) this run.
