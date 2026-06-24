# Phase 2 — Name→position resolution (durable prompt)

> _**Historical record (Claude Code era).** A durable prompt from the original rust-lsp-mcp runtime build under the Claude Code harness; preserved as-is. The project's harness is now IBM Bob — see [the harness port](../planning/bob-harness-port.md)._

**Second-highest risk. Serial, analyzer-bound.** The sole name→symbol bridge.

## Read first
- [implementation-plan.md](../planning/implementation-plan.md) Phase 2 (Option A:
  strict separation, position-based actions).
- Reference: [multilspy-rust-backend-audit.md](../reference/multilspy-rust-backend-audit.md)
  (`request_workspace_symbol` → `UnifiedSymbolInformation`).

## Build
- `find_symbol(name)` runs `workspace_symbol`, returns candidates
  `[{name, kind, file, line, character, container}]`; **zero matches → `not_found`**;
  multiple matches are a normal multi-hit list (no `ambiguous` status).
- Stateless: no symbol handles/IDs cached; positions are passed back directly.
- Reuse the Phase 1 `{status}` envelope. **Land the single 1-indexed↔0-indexed boundary
  helper here** — this is the first place positions cross the boundary, so build the one
  auditable helper now and emit 1-indexed positions through it (Phase 3 reuses it; this
  refines the plan's Phase 3 description of where the helper is *described*, not where it
  must first exist).

## Scope / stop boundary
`find_symbol` only — the action tools (`goto_definition`, etc.) are Phase 3. Stop once
name→position resolution is proven against ripgrep symbols.

## Definition of done (QA gate)
Fast tests for the resolution + `not_found` semantics; **integration gate**: resolve
real ripgrep symbols (incl. overloads/methods surfacing as distinct candidates) and
confirm the **runtime-only `UNVERIFIED`**: the `container` label rust-analyzer attaches
to candidates.

## Adversarial (full red-team)
Falsify: zero-match returning `ok`+empty instead of `not_found`; a name that should
resolve returning nothing; candidate positions that don't round-trip back into a
correct action call; container label assumed but absent.
