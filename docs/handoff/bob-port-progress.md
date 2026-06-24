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
| 1 — `AGENTS.md` spine | 0 | no | done (PR #28 merged; runtime Bob-IDE smoke deferred to end-of-port) |
| 2 — Skills port | 0 | no | in-progress (built; automated QA green; runtime activation deferred) |
| 3 — Modes + orchestration | 0,1,2 | no | in-progress (built; automated QA green; 5 runtime smokes deferred; PR open for review) |
| 4 — Prose rewrite + branding | 1,2,3 | no (single-track) | in-progress (built; verified; PR pending) |
| 5 — Retire Claude scaffolding | 1,2,3,4 | no | not-started |

## Durable per-phase briefs

- Phase 1 — [bob-port-phase-1.md](bob-port-phase-1.md) (`AGENTS.md` spine)
- Phase 3 — [bob-port-phase-3.md](bob-port-phase-3.md) (modes + orchestration; design D1–D7 frozen)

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
- 2026-06-24 — **Runtime testing deferred (user decision):** Bob-IDE manual smokes
  (skill activation, `@`-import resolution in a live session, etc.) can't run until
  the port is complete, so every phase's runtime-only DoD item is deferred to a
  single **end-of-port Bob-IDE verification** rather than gating each phase. Phases
  proceed and merge on automated QA (link-check, frontmatter/schema, structure) +
  static adversarial checks.
- 2026-06-24 — Phase 1 **merged** (PR #28 → `main`); `bob_prototype` fast-forwarded
  to the merge commit. State `done` (runtime smoke deferred per above).
- 2026-06-24 — **Full Bob-fact re-verification** (triggered by a U5 overstatement found
  mid-grill): all 16 items re-vetted via 4-way fan-out vs live `bob.ibm.com/docs`. Every
  item CONFIRMED except **U5** (skills *are* deliberately invokable by request — corrected)
  plus new **U16** (slash commands; custom modes → `/<slug>`). U9 re-confirmed fully SILENT
  (no subtasks page). Corrections folded into the cache + plan Phase 0 outcomes.
- 2026-06-24 — **Phase 3 designed via grill** (D1–D7 frozen) → durable brief
  [bob-port-phase-3.md](bob-port-phase-3.md). Key calls: built-in Orchestrator + a
  `continue-build` *skill* (no `/continue-build` mode — protects delegation); four role
  modes (build/review/qa/adversarial) with write/verify `groups` separation; briefs stay in
  `docs/handoff/` (Orchestrator-None can't read — role modes do); model-per-role + no-parallelism
  named as gaps; adversarial runs in a **fresh Bob session** (U9 silent). 5 runtime-only items
  parked for the end-of-port Bob-IDE pass.
- 2026-06-24 — Phase 4 (**Prose rewrite + branding sweep**, Option C) built. Full Bob
  rewrite of `docs/guide/agentic-coding.md` (orchestration → Bob modes/sequential; "The
  Bob configuration in this repo"). Branding sweep on forward prose: README + guide/docs
  indexes + 3 principle/reference docs (`Claude Code`→`IBM Bob`, `CLAUDE.md`→`AGENTS.md`).
  Era **banners** added to 10 historical Claude-era records (`progress.md`, the 6 per-phase
  prompts + docker-verification, `implementation-plan.md`, `planning-handoff.md`,
  `repo-agnostic-and-docker-launch.md`). Left intentionally: migration-narrative docs,
  3rd-party skill content, and legitimate *client* mentions (Claude Desktop). New **KI-8**
  (devcontainer still provisions the Claude Code extension — out of Phase 1–5 scope); KI-7
  carried (U6 makes the grill-me dup structural). QA green: repo-wide link-check clean (one
  false positive — an illustrative `[x](./x.md)` in a code span); no stale Claude-mechanism
  claims in forward docs (3 remaining refs all accurate: CLAUDE.md-as-retiring + the real
  devcontainer extension).
- 2026-06-24 — Phase 3 (**Modes + orchestration**) built. Added `.bob/custom_modes.yaml`
  (4 role modes — build/review/qa/adversarial — schema-valid, write/verify `groups`
  separation) and `.bob/skills/continue-build/SKILL.md` (self-contained Orchestrator
  playbook; activates on "continue the build"). Rewrote `docs/handoff/`
  `roles.md`+`continue.md`+`adversarial-review.md` to Bob terms (sequential delegation;
  fresh-session adversarial; named no-parallelism + no-model-pinning gaps) and refreshed
  `index.md` (live Bob mechanism / port artifacts / historical Claude-era split).
  Automated QA green: YAML + frontmatter schema valid; link-check clean; no stale
  worktree/parallel *claims* (only honest gap descriptions); no cruft. 5 runtime smokes
  (skill-in-Orchestrator, custom-mode delegation, subtask isolation, Orchestrator-None
  read, `.bob/rules-orchestrator/`) deferred to the end-of-port Bob-IDE pass. PR open;
  paused for review before merge.
- 2026-06-24 — Phase 2 (**Skills port**) built. `mcp-builder` copied **byte-identical**
  to `.bob/skills/mcp-builder/` (SKILL.md + LICENSE.txt + `reference/` + `scripts/`;
  `.DS_Store` excluded, frontmatter unchanged). `grill-me` ported to
  `.bob/skills/grill-me/` with (a) its `description` **retuned for Bob model-activation**
  (`U5` — no explicit user trigger; natural-language grill cues) and (b) its
  cross-folder convention **bundled in-folder** as `project-style.md` (`U6`), links
  rewritten repo-root-relative. Duplication of the grilling-style content logged as
  **KI-7** (reconcile in Phase 4/5). Automated QA green: both manifests' frontmatter
  valid (`name`+`description`); no `.DS_Store`/cruft committed; SKILL.md files reference
  only in-folder material; link-check clean. Runtime activation deferred to end-of-port.
- 2026-06-23 — Phase 1 **built** (working tree, not yet committed/PR'd). Deliverables: root `AGENTS.md` (thin shell, `@`-imports the must-load core); new `agents-core.md` at repo root (conventions pointer/trigger table + hard constraints — the `@`-imported core); `claude-md-layout.md` → `agents-md-layout.md` (`git mv`, rewritten for `AGENTS.md` + the `@`-import three-tier load model). Pointers updated: root `index.md` (added `AGENTS.md`/`agents-core.md`, reframed `CLAUDE.md` as Claude-era/retiring), `docs/conventions/index.md`, `CLAUDE.md`, `docs/guide/agentic-coding.md`, `bob-port-phase-1.md` (fixed the would-be-dangling layout link). **Design note:** the core lives at the *repo root* (not `docs/`) so file-relative == repo-root-relative == the inlined-at-root context — eliminates the `@`-import relative-path ambiguity and keeps a file-relative link-checker green. **Automated QA green:** link-check clean across all touched files; `@./agents-core.md` import resolves and is not inside a code fence; `grep -rn "claude-md-layout"` leaves only 4 historical/prose mentions (planning table + plan rename text + the brief's own deliverable/DoD lines) — no live/dangling pointers. **Remaining for `done`:** manual Bob-IDE smoke (AGENTS.md loads, import resolves in a live session, a bare pointer is read on trigger — the U2 runtime check) and PR. No repo markdown linter exists, so the "markdown lint" fast tier has no automated gate here.
