# Phase 5 — Documentation RAG (durable prompt)

> _**Historical record (Claude Code era).** A durable prompt from the original rust-lsp-mcp runtime build under the Claude Code harness; preserved as-is. The project's harness is now IBM Bob — see [the harness port](../planning/bob-harness-port.md)._

**Off the LSP path** — depends only on Phase 0, so it may run **parallel to Phases 3+4**
(but must not drive the analyzer at the same time; it doesn't need it).

## Read first
- [implementation-plan.md](../planning/implementation-plan.md) Phase 5 + the embedding
  cross-cutting decision.
- Reference: [chromadb-default-embedder.md](../reference/chromadb-default-embedder.md)
  (`PersistentClient`; `DefaultEmbeddingFunction` delegates to ONNX all-MiniLM-L6-v2;
  **cosine via `configuration={"hnsw":{"space":"cosine"}}`**; ~256-token window; model
  cache bind-mount).

## Build
- `PersistentClient` at the bind-mount path; collection with cosine distance.
- Structure-aware chunking: split on markdown headers → leaf section + breadcrumb,
  preserve backticked identifiers; size-split sections over the cap (~200 body tokens,
  under the 256 window) on paragraph boundaries; small overlap only on intra-section
  splits.
- Index **all `*.md` recursively**, driven by a configurable glob in settings (default =
  whole repo; `CHANGELOG.md` the first exclusion candidate).
- One `search_docs(query)` tool, `{status}` envelope; rebuilt wholesale by `refresh`.

## Scope / stop boundary
Doc search only. Reuse the envelope; no code-index coupling, no precomputed code↔doc
links. Stop once `search_docs` returns relevant chunks over ripgrep's markdown.

## Definition of done (QA gate)
Fast tests for chunking (header split, breadcrumb, size cap, overlap) with a faked/local
embedder; **integration gate**: build the store over ripgrep's `*.md` and confirm
sensible retrieval; verify the model cache lands on the bind mount (download-once).

## Adversarial (full red-team)
Falsify: chunks exceeding the 256-token window (silent MiniLM truncation); lost
backticked identifiers; breadcrumbs dropped; distance metric not actually cosine; model
re-downloading on rebuild (cache off-mount); `search_docs` empty handling vs envelope.
