# Bob port — Phase 1 durable brief: the `AGENTS.md` spine

Self-contained prompt to execute **Phase 1** of the Bob harness port from a cold
session. Read this + the plan + the tracker, then build. Do not reopen settled
decisions.

## Resume kickoff (paste into the new session)

> Resume the Bob harness port at **Phase 1**. Read `docs/planning/bob-harness-port.md`
> (plan + settled decisions), `docs/handoff/bob-port-progress.md` (tracker), and
> `docs/handoff/bob-port-phase-1.md` (this brief), then build Phase 1 — the
> `AGENTS.md` spine. We are on branch `bob_prototype`.

## Where we are

- Branch `bob_prototype`, level with `main` at `ad636e1` (plan + Phase 0 merged).
- **Phase 0 (verification) is `done`** — Bob facts confirmed vs the live `bob.ibm.com`
  docs and cached in [bob-harness-capabilities.md](../reference/bob-harness-capabilities.md).
- Goal of the whole effort: replace the Claude Code harness with an IBM Bob one,
  **behavior-parity** bar, naming near-equivalents where Bob can't match.

## Settled decisions that bind Phase 1 (do not reopen)

- **#1 Replace, not coexist** — Claude scaffolding is retired in Phase 5, not now.
- **#2 Parity = behavior/intent** — preserve what `CLAUDE.md` achieves.
- **#3 Instruction spine → root `AGENTS.md`** using `@`-imports; `.bobrules` /
  `.bob/rules-{mode}/` reserved for mode-scoped rules (Phase 3, not here).

## Verified facts this phase stands on (from Phase 0)

- **`@`-import syntax** (`U1`): `@./file.md`, `@../file.md`, `@./sub/file.md`,
  `@/abs/path.md`. Imports **recurse** (default max depth 5). An `@path` inside a
  fenced code block is **ignored**.
- **Link-following is NOT guaranteed** (`U2`, runtime-only): Bob documents inclusion
  *only* via `@`-import. **Rule: anything that MUST reach the agent every session is
  an `@`-import, not a bare markdown link.**
- **Loading model** (`U4`): root-project `AGENTS.md` loads every session; precedence
  is mode-rules → `AGENTS.md` → workspace rules, all **accumulated** (not replaced).
- **`/init` exists** (`U3`) and would generate a root `AGENTS.md` + per-mode files,
  but **overwrite behavior is undocumented** — do **not** run `/init` over the repo
  blind; author `AGENTS.md` by hand (or run `/init` only in a scratch copy to learn
  the shape).

## The key Phase 1 design tension — resolve this first

`CLAUDE.md`'s discipline is **thin**: a pointer index that stays small, with detail
in `docs/` read **on demand**. But `@`-import **inlines** the imported file into
every session — importing all of `docs/` would bloat context and defeat "thin."

So Phase 1 must decide the split:
- **`@`-import only the always-needed core** (the trigger/pointer index itself, the
  hard constraints) so it reliably loads; and
- **leave detailed docs as on-demand references** the agent opens when a trigger
  fires — accepting that on-demand reading rests on the `U2` runtime-only behavior.

Recommended starting position: a thin root `AGENTS.md` that (a) inlines the
constraints + the pointer/trigger table via `@`-import of a small
`docs/conventions/`-style core, and (b) lists the deeper docs as "read when trigger
X applies" pointers. **Then smoke-test in Bob IDE** whether it opens a pointed-to
doc on trigger — that test is the phase's real adversarial check (see below).

## Deliverables

1. **Root `AGENTS.md`** mirroring `CLAUDE.md`'s thin index — navigation, conventions
   pointers, highest-risk areas, settled decisions, constraints — adapted to Bob and
   using `@`-imports for must-load content per the split above.
2. **Retarget `claude-md-layout.md` → `agents-md-layout.md`** (the "keep it thin"
   convention), rewritten for `AGENTS.md` + `@`-import semantics; update every
   pointer to it (`CLAUDE.md`/`AGENTS.md`, `docs/conventions/index.md`, and any doc
   that links it).
3. **Index hygiene**: update `docs/conventions/index.md` and any other index touched.
4. Leave `CLAUDE.md` in place for now (retired in Phase 5) — but the new `AGENTS.md`
   is the source of truth going forward.

## Definition of done (QA gate)

- Fast tier: markdown lint + link-check pass; no dangling pointers after the rename.
- **Manual smoke in Bob IDE**: `AGENTS.md` loads; `@`-imports resolve (content
  present in context); a trigger-pointer actually causes the deeper doc to be read.
- `grep -rn "claude-md-layout"` returns no stale references.

## Adversarial intensity: medium

Falsify the load model, don't assume it. Specifically try to show an `@`-import
**silently dropped** (e.g. wrong relative path, or content placed in a code fence),
and show whether a bare pointer doc is **never read** without an `@`-import — if so,
promote it to an import or restructure. Record the result; if link-following proves
unreliable, that's a `CORRECTION` to fold into the plan before Phase 2.

## Do NOT

- Run `/init` against the live repo (undocumented overwrite — `U3`).
- Build `.bob/skills/`, custom modes, or `.bob/mcp.json` — those are Phases 2–3.
- Reopen settled decisions #1–#8.

## Pointers

- Plan: [bob-harness-port.md](../planning/bob-harness-port.md)
- Tracker (update on completion): [bob-port-progress.md](bob-port-progress.md)
- Verified Bob facts: [bob-harness-capabilities.md](../reference/bob-harness-capabilities.md)
- Source to mirror: [CLAUDE.md](../../CLAUDE.md) + [claude-md-layout.md](../conventions/claude-md-layout.md)
