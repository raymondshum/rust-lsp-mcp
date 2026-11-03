# Rust LSP Navigation MCP Service — Agent Instructions

Read-only Rust code navigation over LSP exposed via MCP, with co-located documentation search.
Built with Python using standard input/output (stdio) transport.
Settled architecture decisions are recorded in planning documents; do not reopen without new technical information.

## Navigation

Start at [index.md](index.md) and follow directory-level index files.
**Keep indexes synchronized**: When adding or moving documentation files, update the corresponding `index.md` in the same step.

**Implementation tracking**: Autonomous agents execute work per [docs/handoff/](docs/handoff/index.md).
- Recurring task kickoff: "Continue the build per docs/handoff/continue.md."
- The orchestrator owns [progress.md](docs/handoff/progress.md) as the single source of truth.

## Conventions

Refer to specific conventions as triggers apply. The [delivery lifecycle](docs/conventions/lifecycle.md) defines the core progression: Grill → Plan → Verify → Implement → Document.

- [Agent instructions layout](docs/conventions/claude-md-layout.md): Keep instructions thin. Place full guidance in `docs/`.
- [Working style](docs/conventions/working-style.md): Propose options with recommendations and durable rationales.
- [Research policy](docs/conventions/research-policy.md): Consult Context7 and first-party docs before relying on model memory. Inspect installed source code when docs are silent.
- [Caching & docs layout](docs/conventions/caching.md): Store durable knowledge in `docs/`, not local memory.
- [Documentation writing](docs/conventions/documentation-writing.md): Use ground → contract → write → verify cycles for human-facing docs.
- [Verification pass](docs/conventions/verification-pass.md): Resolve unverified claims before starting implementation.
- [Phasal plan](docs/conventions/phasal-plan.md): Required contract for planning outputs (phases, dependencies, file partitions).
- [Implementation cycle](docs/conventions/implementation-cycle.md): Follow build → review → QA → adversarial → PR → record.
- [Design grilling](docs/conventions/grill-me.md): Interview and resolve design ambiguities before planning.
- [Known issues](docs/impl/known-issues.md): Living register of design questions and active workarounds.

## High-Risk Areas

Readiness gating and symbol name-to-position mapping carry core implementation risks. See Phases 1 and 2 of [implementation-plan.md](docs/planning/implementation-plan.md).

## Settled Decisions

Consult the "Settled architecture" section in [implementation-plan.md](docs/planning/implementation-plan.md).

## Hard Constraints

1. **Lightweight CI**: Free-tier GitHub Actions has monthly quotas. CI runs only linter, type checks, and fast tests. Heavy integration tests run locally as a QA gate.
2. **Clean Host**: Toolchains and dependencies run inside the development container. Avoid installing project dependencies on the host machine.
3. **Download Once**: Persistent bind mounts cache models, rust-analyzer binaries, and cargo artifacts across container restarts.
