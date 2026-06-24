# Rust LSP Navigation MCP Service — Project Instructions

Read-only Rust code navigation over LSP, exposed via MCP, plus a co-located
documentation RAG tool. Python. Stdio transport, single host. See the
engineering handoff for full design rationale; the confirmed decisions there
are settled and should not be relitigated without new information.

This is the project's instruction spine — the root `AGENTS.md` Bob loads into
every session. It is intentionally **thin**: pointers, not long instructions.
The behavior-critical core (conventions pointer/trigger table + hard constraints)
is `@`-imported just below so it is *guaranteed* in context; deeper docs are
on-demand pointers, read when their trigger fires. Before restructuring this
file or the core, read
[docs/conventions/agents-md-layout.md](docs/conventions/agents-md-layout.md).

## Navigation

Start at [index.md](index.md); traverse index files to find docs. **Keep
indexes current:** when adding or moving a file under `docs/`, update the
`index.md` at that level in the same step.

**Implementation handoff:** Bob executes the plan via
[docs/handoff/](docs/handoff/index.md). Recurring kickoff: "Continue the build per
docs/handoff/continue.md." The orchestrator owns
[progress.md](docs/handoff/progress.md) (the single source of truth for build state).

## Always-load core

The conventions pointer/trigger table and the hard constraints are imported here
so they reach the agent every session even if no pointer is followed:

@./agents-core.md

## Highest-risk areas

Readiness gating and name→position resolution carry the project's real risk —
detail in Phase 1 / Phase 2 of
[implementation-plan.md](docs/planning/implementation-plan.md).

## Settled decisions (do not reopen without new info)

See "Settled architecture" in
[implementation-plan.md](docs/planning/implementation-plan.md).
