# Phase 1 — Readiness gating (durable prompt)

**Highest-risk phase. Serial, analyzer-bound.** Prove the warm-analyzer + fail-fast
behavior before any tool layers on top.

## Read first
- [implementation-plan.md](../planning/implementation-plan.md) Phase 1 + the
  `{status}` envelope section.
- Reference: [multilspy-readiness.md](../reference/multilspy-readiness.md),
  [multilspy-rust-backend-audit.md](../reference/multilspy-rust-backend-audit.md)
  (override contract; instantiate the subclass directly — `create()` hard-codes
  `RustAnalyzer`), [mcp-python-sdk-server.md](../reference/mcp-python-sdk-server.md)
  (FastMCP, stdio). Run the **mcp-builder skill** first.

## Build
- `RustAnalyzer` subclass overriding `setup_runtime_dependencies()` → return the native
  binary path from Phase 0 (no download); instantiate it directly.
- Run multilspy `start_server()` in a background task held for the server lifetime; keep
  the MCP layer's own readiness flag (`indexing`→`ready`).
- Fail-fast gating: tool calls before ready return `not_ready` immediately (never block,
  never empty).
- The uniform `{status, ...}` envelope with the full vocabulary (`ok` / `not_ready` /
  `not_found` / `error`) as shared infrastructure for all later tools.

## Scope / stop boundary
No navigation tools yet beyond what's needed to exercise readiness. Stop once readiness
+ envelope are proven against the warm analyzer over the ripgrep fixture.

## Definition of done (QA gate)
Fast tests (faked analyzer) for the envelope + gating; **integration gate**: cold-start
the analyzer over ripgrep and prove no call returns a misleading empty before `ready`.

## Adversarial (full red-team)
Falsify: any call sequence during indexing that yields empty/partial instead of
`not_ready`; readiness flag set too early; envelope returning `ok`+empty where
`not_ready`/`not_found` is correct; refresh path leaving readiness wrongly set. Findings
→ regression tests; 2-round rework cap.
