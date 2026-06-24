# Planning Handoff — Rust LSP MCP

> _**Historical record (Claude Code era).** A planning handoff from the original rust-lsp-mcp runtime build under the Claude Code harness; preserved as-is. The project's harness is now IBM Bob — see [the harness port](bob-harness-port.md)._

Read this to resume planning in a fresh conversation. It orients you, states what
is decided, what is still open, and how to continue.

## Goal of these planning sessions

Produce a **detailed implementation plan** that Claude Code will execute to
completion. We are still planning — not yet implementing. The plan lives at
[implementation-plan.md](implementation-plan.md).

## Where we are

- **Dev environment / foundation: fully drilled and captured** in
  [implementation-plan.md](implementation-plan.md) Phase 0.
- **Core service: drilled and captured (2026-06-19).** Phases 1–5, the embedding-
  model choice, and the §9 multilspy rust audit are all resolved in
  [implementation-plan.md](implementation-plan.md), with library findings cached in
  `docs/reference/` (multilspy readiness, ChromaDB default embedder, multilspy rust
  backend audit). Headline resolutions: readiness via multilspy's
  serverStatus/quiescent (it blocks until indexed) + fail-fast not-ready gating;
  uniform `{status}` envelope (`ok`/`not_ready`/`not_found`/`error`); Option A
  position-based action tools with `find_symbol` as the sole name bridge; 1-indexed
  boundary; flat `document_symbols`; unconditional `refresh` + 4-field `status`;
  doc-RAG over ripgrep markdown with header+size chunking; local ChromaDB ONNX
  embedder (cached on a bind mount); rust-analyzer supplied natively in the dev
  container via a multilspy subclass override (Option B).
- **Plan-verification pass: DONE (2026-06-19).** Every spec-level `UNVERIFIED`
  item across the plan and reference docs has been confirmed via Context7 / source
  inspection and flipped to `VERIFIED` with cached `docs/reference/` entries (MCP
  Python SDK, pydantic-settings, uv + setup-uv, ruff, pytest markers, devcontainer
  feature IDs, MCP Inspector; plus chromadb/multilspy refinements). Two material
  corrections: (1) **no official uv devcontainer feature** — use community/va-h or
  layer the first-party uv image; (2) **rust-analyzer has no separate on-disk index
  cache** — relocate via `rust-analyzer.cargo.targetDir` + `CARGO_HOME`, not a
  dedicated cache dir. Only runtime-only / intentionally-deferred items remain
  `UNVERIFIED` (annotated inline). **No code is implemented yet** — ready for build.

## Vision (holding)

A rough prototype against **ripgrep**. Success = an assistant names something in
the code and gets back where it's defined, its type, and its uses — and never a
misleading empty answer while indexing. Build **risk-first**; doc search last.

## Working agreements (apply throughout)

- **Context7 over training.** For any library/API detail (MCP Python SDK,
  multilspy, ChromaDB, rust-analyzer/LSP, uv, ruff, ty, pydantic-settings), query
  Context7 before trusting memory; cache findings in `docs/reference/` (version +
  date stamped). See `CLAUDE.md` (the project instructions; now `AGENTS.md`). For MCP server construction,
  use the `mcp-builder` skill first.
- **Verify the plan.** Before the plan is final, cross-check **every** command,
  version, flag, and config snippet against current docs via Context7. Items in
  the plan are marked `VERIFIED` or `UNVERIFIED`.
- **Grilling style.** When grilling, follow
  [docs/conventions/grill-me.md](../conventions/grill-me.md): overview of
  decisions → align on vision → drill one question at a time (with a recommended
  answer each) → realign at the end. Plain language, no jargon, nested bullets.
  Explore the codebase to answer a question instead of asking when possible.
- **Navigation.** Start at the root `index.md`; traverse index files. Keep indexes
  current when adding/moving files under `docs/`.

## Source of truth for design rationale

The original **engineering handoff** (the §-numbered document) holds the settled
architecture and the rationale. Settled decisions (handoff §3) are summarized at
the bottom of [implementation-plan.md](implementation-plan.md) and **must not be
reopened without new information**.

## How to resume

The core-service grilling and the **plan-verification pass** are **done**. Execution
scaffolding now exists: Claude Code runs the build via
[docs/handoff/](../handoff/index.md) — durable per-phase prompts, an orchestrator-owned
[progress.md](../handoff/progress.md) tracker, the [continue.md](../handoff/continue.md)
dispatcher, and an adversarial review gate. Kickoff is a single recurring message to
Claude Code: **"Continue the build per docs/handoff/continue.md."** Risk-first order
(Phase 0 → readiness → name→position → tools → doc-RAG) is encoded in the tracker's
dependency graph.
