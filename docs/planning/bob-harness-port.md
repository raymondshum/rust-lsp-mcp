# Bob harness port — phasal plan

Re-tool the development **harness** (the agent-facing scaffolding) away from
Claude Code and onto **IBM Bob** — IDE first-class, CLI a stretch goal. This is
the exit artifact of the **Plan** stage for the `bob_prototype` branch. It freezes
the decisions made during the grill of 2026-06-23 and lays out the phases the
[implementation cycle](../conventions/implementation-cycle.md) executes.

Scope is the *harness*, not the runtime: the rust-lsp-mcp service itself
(LSP-over-MCP + docs RAG) is unchanged. What changes is how the project is
*built by an agent* — instructions, skills, orchestration, memory, permissions,
and the convention prose that describes them.

> **Verification posture.** IBM Bob facts in this plan are grounded in the **live
> `bob.ibm.com/docs` site**, treated as authoritative. Context7's
> `/websites/bob_ibm` index is **incomplete** (it omitted the Skills feature
> entirely), so it is used only as a first pass. Phase 0 re-confirms every
> `UNVERIFIED` item against the live site before any building.

> **Status: Phase 0 (verification pass) complete — 2026-06-23.** Findings cached in
> [bob-harness-capabilities.md](../reference/bob-harness-capabilities.md). Most items
> `VERIFIED`; several `CORRECTION`s and runtime-only residues below
> ([Phase 0 outcomes](#phase-0-outcomes)). No correction is fatal; three carry design
> impact (skills are model-activated + IDE-only; subtask cold-context is runtime-only).

## Settled decisions (frozen — do not reopen without new info)

1. **Replace, don't coexist.** The Bob harness supersedes the Claude harness; the
   Claude scaffolding (`CLAUDE.md`, `.claude/`) is retired at the end (Phase 5).
2. **Parity bar = behavior/intent.** For each Claude artifact we preserve *what it
   achieves*, using whichever Bob mechanism fits — and we **name the near-equivalent
   explicitly** wherever Bob cannot reach parity.
3. **Instruction spine → `AGENTS.md`.** `CLAUDE.md` maps to a root `AGENTS.md` (a
   Bob-read context file) keeping the thin-pointer-index style via **`@`-imports**.
   `.bobrules` / `.bob/rules-{mode}/` are reserved for mode-scoped rules.
4. **Skills → Bob skills, ~1:1.** `.claude/skills/<name>/` → `.bob/skills/<name>/`
   (`SKILL.md` + bundled `reference/`/`scripts/`), frontmatter unchanged. Custom
   modes are reserved for roles, **not** skill porting.
5. **Orchestration → Orchestrator mode + a `continue-build` skill, sequential.**
   Our lean orchestrator → Bob's **Orchestrator mode** (`groups: None`, delegates);
   build/review/QA/adversarial roles → **custom modes** invoked as subtasks **one
   at a time**; the build-loop playbook lives in a **`continue-build` skill**
   (bundling the per-phase briefs). Parallel fan-out + git-worktree isolation are
   **dropped** (no parallel writers). *Near-equivalent gap: no parallelism* — cheap
   here because the live-analyzer rule already serialized most build work.
6. **Memory → `docs/`; accept the gap.** Bob has **no** per-user persistent
   auto-recall memory store. Durable knowledge routes to `docs/` (already the
   convention). A gitignored `AGENTS.local.md` `@`-import is reserved as an optional
   later bolt-on for private per-user notes.
7. **MCP config stays per-user/gitignored.** `.bob/mcp.json` is developer-local
   (mirrors today's uncommitted `settings.local.json`); the expected dev MCP setup
   (Context7) is *documented* in `docs/`, not committed. Role tool-scoping rides on
   committed custom-mode `groups`. Per-user auto-approval mode stays personal.
8. **Prose: hybrid rewrite (Option C).** Rewrite only mechanism-defining docs to
   Bob; lightly edit tool-agnostic principle docs; **preserve historical build
   records** (Phases 0–5, `progress.md`, `docs/planning/*`) as honest Claude-era
   artifacts with a one-line banner. Branding sweep ("Claude Code" → "IBM Bob")
   applies to **forward-looking prose only**.

## Claude → Bob mapping

| Claude Code artifact | IBM Bob target | Parity |
|---|---|---|
| `CLAUDE.md` (thin instruction index) | root **`AGENTS.md`** + `@`-imports into `docs/` | Full (mechanism arguably cleaner) |
| `claude-md-layout.md` convention | `agents-md-layout.md` (retargeted) | Full |
| `.claude/skills/grill-me/` | `.bob/skills/grill-me/` | Full (1:1 copy) |
| `.claude/skills/mcp-builder/` (+ `reference/`,`scripts/`) | `.bob/skills/mcp-builder/` (bundle preserved) | Full |
| Lean orchestrator (coordinator) | **Orchestrator mode** (`groups: None`) | Full |
| Build/reviewer/QA/adversarial agents | **custom modes** (`.bob/custom_modes.yaml`) | Full (personas) |
| Parallel fan-out + git worktrees | sequential subtask delegation | **Gap: no parallelism** |
| `docs/handoff/continue.md` dispatcher | **`continue-build` skill** (bundled per-phase briefs) | Full |
| Per-user file memory (`~/.claude/.../memory/`) | `docs/` (+ optional `AGENTS.local.md`) | **Gap: no auto-recall store** |
| `.claude/settings.local.json` allowlist | `.bob/mcp.json` `alwaysAllow` + auto-approval (per-user) | Full (stays uncommitted) |
| Per-role permissions | custom-mode `groups` (read/edit+`fileRegex`/command) | Full |

## Named parity gaps (honest residue)

- **No parallel orchestration.** Bob's Orchestrator delegates **sequentially**.
  Impact is narrow: the *single serialized rust-analyzer* rule already forced most
  build work to run one-at-a-time; only the analyzer-free slice (docs, independent
  edits) loses concurrency.
- **No per-user persistent memory.** Bob's "memory" features are static context
  (`@`-import, `/memory`). Mitigated by the existing `docs/`-first convention.

## Phases (risk-first)

Unless noted, phases are **sequential, single-track** (Bob has no fan-out, and the
analyzer rule already serializes contended work). Each phase's durable brief is
embedded below; full handoff prompts split into `docs/handoff/` at build start.
Progress is tracked in [bob-port-progress.md](../handoff/bob-port-progress.md).

### Phase 0 — Verification pass (highest risk)
- **Depends on:** none. **Parallelizable:** no.
- **Goal:** confirm every `UNVERIFIED` item (below) against the **live Bob site**.
  The whole plan rests on Bob facts; retire that risk first.
- **Definition of done:** each `UNVERIFIED` item flipped to `VERIFIED` (with a
  `docs/reference/` citation, stamped library+date) or escalated as a blocker with a
  revised approach. Fast tier: docs link-check.
- **Adversarial intensity:** full — actively try to *falsify* each Bob-capability
  assumption (esp. boomerang context isolation U9, cross-folder skill reads U6).

### Phase 1 — `AGENTS.md` spine
- **Depends on:** Phase 0 (U1–U4). **Parallelizable:** no.
- **Goal:** author root `AGENTS.md` mirroring `CLAUDE.md`'s thin index, using
  `@`-imports for the `docs/` pointers; retarget `claude-md-layout.md` →
  `agents-md-layout.md`.
- **Definition of done:** Bob loads `AGENTS.md` and resolves the imports; the thin
  index reaches the agent (manual smoke in Bob IDE). Fast tier: markdown lint +
  link-check.
- **Adversarial intensity:** medium — verify imports actually load vs. silently drop.

### Phase 2 — Skills port
- **Depends on:** Phase 0 (U5–U8). **Parallelizable:** no (small).
- **Goal:** copy both skills to `.bob/skills/`; fix any cross-folder reference
  (grill-me → `docs/conventions/grill-me.md`) per the verified mechanism.
- **Definition of done:** both skills activate in Bob (auto and/or explicit per U5);
  `mcp-builder` reads its bundled `reference/`/`scripts/`. Fast tier: frontmatter
  schema check.
- **Adversarial intensity:** medium.

### Phase 3 — Modes + orchestration
- **Depends on:** Phases 0–2 (U9–U11). **Parallelizable:** no.
- **Goal:** define the Orchestrator mode + role custom modes in
  `.bob/custom_modes.yaml` (with `groups`); build the `continue-build` skill holding
  the per-phase build-loop playbook; rewrite `handoff/roles.md` and
  `handoff/continue.md` to Bob terms.
- **Definition of done:** Orchestrator delegates a sample task through
  build→review→QA→adversarial sequentially; adversarial subtask runs in isolated
  context (U9). Heavier gate: a dry-run delegation on a trivial change.
- **Adversarial intensity:** full — this is the parity core.

### Phase 4 — Prose rewrite (Option C) + branding sweep
- **Depends on:** Phases 1–3. **Parallelizable:** conceptually yes (per-doc), but
  run single-track. **Partition:** one doc per pass; mechanism docs vs. principle
  docs vs. historical records (banner only).
- **Goal:** rewrite mechanism docs to Bob; light terminology edits on principle
  docs; banner historical records; sweep "Claude Code" → "IBM Bob" in forward prose;
  update all affected `index.md` pointers (including `agentic-coding.md`).
- **Definition of done:** no stale Claude-mechanism claims in forward-looking docs;
  link-check passes; historical records carry the era banner.
- **Adversarial intensity:** light — contract/consistency check.

### Phase 5 — Retire Claude scaffolding
- **Depends on:** Phases 1–4 all `done`. **Parallelizable:** no.
- **Goal:** remove `CLAUDE.md` and `.claude/` (skills now live under `.bob/`);
  final repo-wide consistency pass.
- **Definition of done:** repo has no Claude-harness files; full doc link-check
  green; a fresh-session smoke test in Bob IDE exercises the lifecycle end-to-end.
- **Adversarial intensity:** medium — confirm nothing forward-looking still
  references the removed files.

## Phase 0 outcomes

Verified 2026-06-23 against the live Bob site; full quotes + sources in
[bob-harness-capabilities.md](../reference/bob-harness-capabilities.md).

| Item | Verdict | Note |
|---|---|---|
| U1 `@`-import semantics | ✅ VERIFIED | `@./`,`@../`,`@/abs`; recurses, max depth 5; glob undocumented (runtime-only) |
| U2 plain-link auto-follow | ⏳ runtime-only | **Design rule: use `@`-imports for must-load content; don't rely on link-following** |
| U3 `/init` output | ✅ / ⏳ | Generates root `AGENTS.md` + `.bob/rules-{mode}/AGENTS-{mode}.md`; overwrite behavior undocumented |
| U4 hierarchy & precedence | ✅ VERIFIED | mode-rules → AGENTS.md → workspace rules; accumulates, not replaces |
| U5 skill invocation | ⚠️ CORRECTION (2026-06-24) | Activation is **"based on your request and the skill's description"** — so a skill **IS deliberately invokable by phrasing the request** (then approving); what's absent is a `/skill-name` slash command. Earlier "no user trigger / approve-only" was overstated |
| U16 slash commands (new) | ✅ VERIFIED (2026-06-24) | `/` menu: built-ins `/init`,`/review`,`/create-pr`; `/code`·`/ask` switch mode; **custom modes appear as `/<slug>`**; skills are **not** in the slash menu |
| U6 out-of-folder file refs | ⏳ runtime-only | **Design rule: bundle skill material *inside* the skill folder** |
| U7 skill approval setting | ✅ VERIFIED | Settings → Auto-Approve → "Skills" toggle; no config-key documented |
| U8 mechanism inventory | ⚠️ CORRECTION | Also: custom rules, context mentions, code actions. **Skills are IDE-only** (no Shell skills) |
| U9 subtask cold-context | ⏳ runtime-only | Only "separate task instances" documented; **isolation + result-return unconfirmed — test in Phase 3** |
| U10 built-in modes | ⚠️ CORRECTION | Five: Code, Ask, Plan, Advanced, Orchestrator (**no Debug/Architect**); Orchestrator *can* delegate to custom modes |
| U11 `custom_modes.yaml` | ✅ VERIFIED | `customModes:` array; no-tools mode supported (Orchestrator=None); `command` has no restrict-syntax |
| U12 `/memory` + autowrite | ✅ / ⏳ deferred | `/memory refresh`/`show` exist; autonomous write to imports undocumented (memory→`docs/` anyway) |
| U13 `.bob/mcp.json` | ✅ VERIFIED | `mcpServers`+`command`/`args`/`cwd`/`env`/`url`/`alwaysAllow`/`disabled`; **global is `~/.bob/mcp_settings.json`** |
| U14 `groups` schema | ⚠️ CORRECTION | Values: read, edit(+`fileRegex`), command, browser, mcp, **`skill`** |
| U15 auto-approval | ✅ VERIFIED | UI toolbar toggles (11 actions); no config-key; per-user (decision #7) |

### Corrections with design impact (carry into later phases)

- **Skills activate "based on your request and the skill's description" (U5,
  corrected 2026-06-24) + reference only in-folder files (U6).** A skill **is**
  deliberately invokable — you phrase a request matching its `description` (there is
  no `/skill-name` slash command, but custom *modes* are slash-invokable as `/<slug>`,
  U16). Phase 2 still (a) wrote `grill-me`'s `description` to cover grill-style
  requests — which serves **both** auto- and deliberate-by-request activation — and
  (b) **copied `docs/conventions/grill-me.md` into `.bob/skills/grill-me/`** rather
  than pointing at the repo path.
- **Skills are IDE-only (U8).** The **CLI stretch goal** can't reuse the skills as-is —
  in Bob Shell the three skills (`grill-me`, `mcp-builder`, `continue-build`) would
  need re-expression as **custom modes + slash commands**. Recorded as a stretch-scope
  constraint, not a Phase 1–5 blocker.
- **Subtask cold-context isolation is undocumented (U9, re-confirmed fully SILENT
  2026-06-24 — no subtasks page even exists).** The docs promise **no** isolation and
  **no** result-return, so the adversarial gate's independence **cannot** rest on Bob
  subtasks. Phase 3 designs the adversarial pass to run in a **fresh Bob session (or
  `/clear`ed context) as the primary path** — not a fallback — and treats subtask
  isolation as a runtime nice-to-have to relax toward only if a later test confirms it.
- **Built-in mode list corrected (U10):** no Debug/Architect — the role custom modes
  in Phase 3 layer on Code/Ask/Plan/Advanced/Orchestrator.

Remaining residue is all `UNVERIFIED — runtime-only` (confirmable only by exercising
Bob) or `intentionally deferred` (U12) — none blocks Phases 1–5.

## Related

- [phasal-plan.md](../conventions/phasal-plan.md) — the contract this plan satisfies.
- [bob-port-progress.md](../handoff/bob-port-progress.md) — single-source tracker.
- [verification-pass.md](../conventions/verification-pass.md) — Phase 0's method.
- [agentic-coding.md](../guide/agentic-coding.md) — the guide page Phase 4 rewrites.
