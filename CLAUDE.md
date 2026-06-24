# Rust LSP Navigation MCP Service — Project Instructions

Read-only Rust code navigation over LSP, exposed via MCP, plus a co-located
documentation RAG tool. Python. Stdio transport, single host. See the
engineering handoff for full design rationale; the confirmed decisions there
are settled and should not be relitigated without new information.

## Navigation

Start at [index.md](index.md); traverse index files to find docs. **Keep
indexes current:** when adding or moving a file under `docs/`, update the
`index.md` at that level in the same step.

**Implementation handoff:** Claude Code executes the plan via
[docs/handoff/](docs/handoff/index.md). Recurring kickoff: "Continue the build per
docs/handoff/continue.md." The orchestrator owns
[progress.md](docs/handoff/progress.md) (the single source of truth for build state).

## Conventions

Brief pointers — open the file when its topic or trigger applies. The
[delivery lifecycle](docs/conventions/lifecycle.md) is the **spine** (Grill → Plan →
Verify → Implement → Document); the per-stage docs below implement it.

- [AGENTS.md layout](docs/conventions/agents-md-layout.md) — read before editing the
  instruction spine: keep it thin (pointers + brief description/trigger); detail lives in `docs/`.
- [working style](docs/conventions/working-style.md) — how to propose approaches and decide.
- [research policy](docs/conventions/research-policy.md) — read before trusting memory
  on any library/API detail. **Essence:** prefer Context7 (and current first-party
  docs) over training knowledge; when docs are silent, read the package source.
- [caching & docs/ layout](docs/conventions/caching.md) — where learned patterns go
  (memory vs `docs/`), and the `docs/` category folders.
- [documentation writing](docs/conventions/documentation-writing.md) — read before writing
  or revising human-facing docs (README, `docs/guide/`). The loop: **ground (fan-out
  explorers → fact-sheets) → shared contract → write (one page/agent) → verify**.
- [verification pass](docs/conventions/verification-pass.md) — read before a
  "verify the plan" / "flip UNVERIFIED to VERIFIED" pass: inventory → confirm via
  Context7/source → cache + annotate the residue.
- [phasal plan](docs/conventions/phasal-plan.md) — the output contract for planning:
  what a grill/plan session must produce (phases, deps, file-ownership partitions,
  definition-of-done, adversarial intensity) to be implementable.
- [implementation cycle](docs/conventions/implementation-cycle.md) — the standard build
  loop (build → review → QA → adversarial → PR → record), one phase per pass.
  rust-lsp-mcp's instance is the handoff dispatcher [continue.md](docs/handoff/continue.md).
- Grilling a plan/design: first read [grill-me.md](docs/conventions/grill-me.md). The
  loop is **grill (decide) → `UNVERIFIED` inventory → [verification pass](docs/conventions/verification-pass.md)**.
- [known issues](docs/impl/known-issues.md) — living register of open design / documentation
  issues. **Review** at the start of a grill/plan session, at each phase's record step, and
  when editing a module an open issue names.

## Highest-risk areas

Readiness gating and name→position resolution carry the project's real risk —
detail in Phase 1 / Phase 2 of
[implementation-plan.md](docs/planning/implementation-plan.md).

## Settled decisions (do not reopen without new info)

See "Settled architecture" in
[implementation-plan.md](docs/planning/implementation-plan.md).

## Constraints

- **CI must stay light.** Free-tier GitHub Actions has a monthly quota. CI runs
  lint, type checks, and fast tests only; heavy/integration tests run locally as a
  named QA gate, never in CI.
- **Host stays clean.** All toolchains, binaries, and services live in the dev
  container; nothing is installed on the host macOS. Prefer dev-container features /
  in-container installs over anything that touches the host.
- **Download once.** Heavy downloaded artifacts (models, analyzer binaries,
  build/index caches) go on observable, gitignored bind mounts so they survive
  rebuilds and aren't refetched.
