# Phases 3+4 — Navigation + operational tools (durable prompt)

> _**Historical record (Claude Code era).** A durable prompt from the original rust-lsp-mcp runtime build under the Claude Code harness; preserved as-is. The project's harness is now IBM Bob — see [the harness port](../planning/bob-harness-port.md)._

**Lower risk, shared envelope. Intra-phase parallel** on the fast-test tier; the
integration gate is serial on the one analyzer.

## Read first
- [implementation-plan.md](../planning/implementation-plan.md) Phase 3 (tool schemas,
  1-indexed boundary helper, flat `document_symbols`, workspace-relative paths) and
  Phase 4 (`refresh`, `status`).

## Build (partition by file ownership for parallel agents)
Navigation tools, each its own file/owner:
- `document_symbols(file)` → flat `[{name, kind, line, character, container}]`.
- `goto_definition(file, line, character)` → `definitions`; none → `not_found`.
- `find_references(file, line, character[, include_declaration=false])` → `references`;
  zero → **`ok`+empty** (real "no callers").
- `hover(file, line, character)` → rust-analyzer markdown string; nothing → `not_found`.
- The **1-indexed↔0-indexed boundary helper** is **reused from Phase 2** (it lands
  there, where positions first cross the boundary). Do not re-implement; the nav tools
  build on the existing helper.

Operational tools:
- `refresh` — unconditional teardown + wholesale re-index (never wipes the analyzer's
  on-disk cargo cache; blocks-until-quiescent again).
- `status` — `state` / `indexed_commit` / `current_commit` / `stale` (with the
  uncommitted-edits caveat in the tool description).

## Parallel/serial split
The boundary helper first (blocks the others). Then the 4 nav tools fan out in parallel
worktrees on **fast tests with a faked analyzer**. `refresh`/`status` are small and
analyzer-touching — keep serial. The **integration gate runs once, serialized.**

## Definition of done (QA gate)
Fast tests for every tool (faked analyzer) incl. the `ok`+empty vs `not_found` split and
boundary round-tripping; **integration gate** over ripgrep for the full discover→act
loop.

## Adversarial (full on nav semantics; light on operational)
Falsify: `find_references` zero → wrong status; off-by-one in the boundary helper;
`hover`/`goto_definition` empty vs `not_found` confusion; `status.stale` wrong vs HEAD;
`refresh` wiping cargo cache or mis-setting readiness.
