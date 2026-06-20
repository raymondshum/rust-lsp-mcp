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

Brief pointers — open the file when its topic or trigger applies.

- [CLAUDE.md layout](docs/conventions/claude-md-layout.md) — read before editing this
  file: keep it thin (pointers + brief description/trigger); detail lives in `docs/`.
- [working style](docs/conventions/working-style.md) — how to propose approaches and decide.
- [research policy](docs/conventions/research-policy.md) — read before trusting memory
  on any library/API detail. **Essence:** prefer Context7 (and current first-party
  docs) over training knowledge; when docs are silent, read the package source.
- [caching & docs/ layout](docs/conventions/caching.md) — where learned patterns go
  (memory vs `docs/`), and the `docs/` category folders.
- [verification pass](docs/conventions/verification-pass.md) — read before a
  "verify the plan" / "flip UNVERIFIED to VERIFIED" pass: inventory → confirm via
  Context7/source → cache + annotate the residue.
- Grilling a plan/design: first read [grill-me.md](docs/conventions/grill-me.md). The
  loop is **grill (decide) → `UNVERIFIED` inventory → [verification pass](docs/conventions/verification-pass.md)**.

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
