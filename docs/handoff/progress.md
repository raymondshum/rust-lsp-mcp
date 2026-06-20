# Build progress tracker

**Single source of truth for "where are we."** The **orchestrator is the sole writer**;
build/reviewer/QA/adversarial agents report results, the orchestrator records them here.
Read by [continue.md](continue.md) to pick the next phase.

## State vocabulary

`not-started` → `authoring` → `awaiting-container-build` (Phase 0 only) →
`in-progress` → `qa` → `adversarial` → `pr-open` → `done`.
(`blocked` = paused for human, with a one-line reason.)

## Gate-zero (handoff self-review)

`gate-zero: passed (2026-06-19)` — adversarial pass over `docs/handoff/` (incl.
`continue.md`) must be `passed` before any phase starts. Values: `not-run` | `passed` |
`blocked: <reason>`. Orchestrator flips it and records the date in the log below.

## Phase status

| Phase | Prompt | Depends on | Parallelizable? | State |
|-------|--------|-----------|-----------------|-------|
| 0 — Foundation | [phase-0-foundation.md](phase-0-foundation.md) | — | No (shared config; serial) | awaiting-container-build |
| 1 — Readiness gating | [phase-1-readiness.md](phase-1-readiness.md) | 0 | No (analyzer-bound, serial) | not-started |
| 2 — Name→position | [phase-2-resolution.md](phase-2-resolution.md) | 1 | No (analyzer-bound, serial) | not-started |
| 3+4 — Nav + operational tools | [phase-3-4-tools.md](phase-3-4-tools.md) | 2 | **Yes** — the 5 tools fan out on the fast-test tier (faked analyzer); integration gate serial | not-started |
| 5 — Doc-RAG | [phase-5-doc-rag.md](phase-5-doc-rag.md) | 0 | **Yes** — off the LSP path; may run parallel to 3+4 | not-started |

## Dependency graph (what the orchestrator may fan out)

```
0 ──> 1 ──> 2 ──> 3+4
└────────────────> 5      (5 needs only Phase 0; can run alongside 3+4)
```

- Cross-phase: strictly the arrows above. Never start a phase whose dependency isn't
  `done`.
- Intra-phase parallelism is allowed **only for analyzer-free tasks** (see
  [roles.md](roles.md)); the live analyzer + integration gate are a single serialized
  resource even when phases overlap (e.g. 3+4 and 5 must not both drive the analyzer at
  once).

## Per-phase log (orchestrator appends)

> One line per state transition: `<date> Phase N → <state> (PR #/notes)`.

- 2026-06-19 Gate-zero → passed (adversarial pass over `docs/handoff/`; 3 must-fixes +
  4 minors applied to `continue.md` and `progress.md`). Build not yet started.
- 2026-06-19 Phase 0 → awaiting-container-build (Beat A authored on `phase0`: devcontainer
  + Dockerfile, 5 bind mounts, pyproject src layout + both launch paths, settings layer +
  env.sample + init.sh, ruff/ty + `.vscode/`, pytest tiers, setup/teardown, CI + env-honesty
  check, `.gitignore`. Uncommitted, pending human review + container build).
