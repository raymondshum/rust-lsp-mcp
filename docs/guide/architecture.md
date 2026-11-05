---
okf_version: "0.1"
type: Guide
title: Architecture
description: How rust-lsp-mcp's server, analyzer, and doc-search pieces fit together and why.
tags: [guide, tier-b, architecture]
timestamp: 2026-11-04T00:00:00Z
source_pins:
  - path: src/rust_lsp_mcp/server.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/core.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/analyzer.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/doc_store.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/doc_chunking.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/envelope.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/positions.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
cite:
  - src/rust_lsp_mcp/positions.py:external_to_lsp
---

[← Back to the README](../../README.md) · [Documentation index](index.md)

# Architecture

This page explains how the rust-lsp-mcp service is put together: what the
pieces are, how they talk to each other, and why a few design choices were
made. No prior knowledge of language servers or AI tooling is required.

---

## 1. The big picture

The service sits between an AI assistant and two information sources:

- **rust-analyzer**: a code-intelligence engine for Rust that can answer
  questions like "where is this function defined?" or "who calls this struct?"
- **A local documentation search**: finds relevant passages in the project's
  Markdown files based on the meaning of a question, not just keywords.

```
AI assistant (MCP client)
        │  (standard input/output)
        ▼
   rust-lsp-mcp server
     ├── rust-analyzer  ── code navigation (definitions, references, hover, symbols)
     └── documentation search ── meaning-based search over the project's Markdown
```

**MCP** stands for Model Context Protocol: a standard way for AI
assistants to call external tools, the same way a web browser calls web APIs.
The server speaks MCP over standard input/output: the AI assistant launches
the server as a subprocess and writes requests to it; the server writes
answers back. No network port is needed.

---

## 2. How a request flows

Here is what happens when an AI assistant asks "find all references to this
function":

1. **The client calls the tool**: it sends a request naming the file, line,
   and column of the function.
2. **Input validation**: the server checks that the file path and position
   make sense before doing any real work.
3. **Readiness check**: the server confirms that rust-analyzer has finished
   indexing the codebase (see section 3). If it has not, the server returns
   a `not_ready` status immediately instead of asking the analyzer.
4. **Position conversion**: line and column numbers are converted from the
   1-based counting humans use to the 0-based counting the protocol expects
   (see section 5).
5. **Ask rust-analyzer**: the server passes the request to rust-analyzer
   through a library called **multilspy** (version 0.0.15). multilspy speaks
   the Language Server Protocol (LSP), the standard way editors talk to
   code-intelligence engines like rust-analyzer.
6. **Translate the answer**: the raw LSP response (0-based positions, URI
   paths) is converted back into a clean, 1-based, human-readable result.
7. **Return a status envelope**: the server wraps the result in a small
   object with a `status` field (see section 4) and returns it to the client.

---

## 3. Readiness: never a misleading empty answer

This is the single most important design rule in the service.

When the server starts, rust-analyzer must first **index** the codebase:
read and understand all the Rust source files. This can take several seconds
to a few minutes depending on the project size. A naive system asked "find
all callers of `foo`" during this window would answer "none found," which
is wrong and looks identical to a real "no callers" result. There is no way
for the caller to tell the difference.

To avoid this trap, the server tracks an explicit readiness state:

- The analyzer starts in the `"indexing"` state.
- It flips to `"ready"` when rust-analyzer signals completion and the connection is active. In [`analyzer.py`](../../src/rust_lsp_mcp/analyzer.py), the `is_ready` property requires both conditions: `state == "ready"` and an active connection.
- If background indexing fails, the state transitions to `"error"`. This is a permanent state until resolved. The `status` tool exposes the error message via `analyzer_error`.
- Tools requiring the index call `require_ready()` in [`core.py`](../../src/rust_lsp_mcp/core.py).
  - While indexing, `require_ready()` returns a `not_ready` envelope immediately.
  - If the analyzer is in an `"error"` state, `require_ready()` returns an `error` envelope. This envelope prompts the caller to invoke `refresh`.

The documentation store uses an identical tri-state model (`"building"`, `"ready"`, `"error"`). `search_docs` never returns partial results during rebuilds. It reports build failures as `error` rather than `not_ready`.

---

## 4. Response format (status envelope)

Every tool returns a response object containing a `status` field, defined in [`envelope.py`](../../src/rust_lsp_mcp/envelope.py):

| Status | Meaning |
|---|---|
| `ok` | The query ran successfully. Empty result sets are valid answers. |
| `not_ready` | The index is currently building. Retry shortly. |
| `not_found` | The requested symbol or file does not exist. |
| `error` | Invalid input, internal failure, or failed indexing. Invoke `refresh` to recover from failed states. |

`not_ready` and `error` envelopes include a machine-readable `recovery` hint (`"fix_input"`, `"refresh"`, `"poll_status"`, or `"unknown"`). Callers can branch on this field directly. `not_ready` always sets `recovery: "poll_status"`. See [Tool reference](tools.md#the-recovery-field) for the full matrix.

### Distinguishing empty results from missing symbols
The difference between `ok` with an empty list and `not_found` conveys specific information:
- `not_found` indicates the target symbol does not exist at that position or name.
- `ok` with an empty list indicates the symbol exists, but contains zero references or children.

List-returning tools (`find_symbol`, `document_symbols`, `find_references`) include `total` and `truncated` fields:
- `total: 0` signals a verified symbol with zero results.
- `truncated: true` indicates results exceeded the 200-item safety cap. See [Tool reference](tools.md#list-caps-total-and-truncated) for details.

---

## 5. Counting from 1, not 0

Humans and most editors count lines and columns starting from 1: "line 5,
column 3." The Language Server Protocol counts from 0: the same position
is "line 4, character 2."

The server always presents 1-based positions to the outside world and always
sends 0-based positions to rust-analyzer internally. The conversion happens
in one small module, [`positions.py`](../../src/rust_lsp_mcp/positions.py):
`external_to_lsp` converts an inbound (1-indexed) position to LSP's 0-indexed
form, and `lsp_to_external` converts an outbound one back. That gives a
single place to audit and a single place to fix if anything ever goes wrong.
No tool module does its own arithmetic on positions.

---

## 6. How documentation search works

The documentation search finds Markdown passages that are *meaningfully*
close to a question, not just textually similar. Here is the pipeline,
step by step:

**(a) Chunking.** The project's Markdown files are split into small,
self-contained pieces called "chunks." Each chunk is short enough to be
processed in one go (kept under 200 tokens, a deliberate, conservative cap
that stays well within the embedding model's 256-token input limit, so no text
is ever silently cut off). Each chunk is labeled with a
breadcrumb that records its position in the document hierarchy, for example,
`GUIDE.md > Configuration > Ignoring files`.

**(b) Embedding.** Each chunk is converted into a list of numbers called an
"embedding." The numbers capture the meaning of the text in a way that can
be compared mathematically. This is done by a small model called
**all-MiniLM-L6-v2**, which runs entirely on the local CPU using the ONNX
(Open Neural Network Exchange) runtime, a standard format for running
machine-learning models efficiently. The model is baked into the server
image at build time, so no external service or internet connection is
needed at runtime.

**(c) Storage.** The chunks and their embeddings are stored in a local
database called **ChromaDB**, configured to use cosine distance as its
similarity measure. Cosine distance is a way of comparing two embeddings
where a value of 0 means "identical meaning" and larger values mean "less
similar." The database can quickly find the chunks whose embeddings are
closest to any given query.

**(d) Search.** When a search query arrives, it is embedded the same way.
The database returns the chunks with the smallest cosine distance to the
query, the closest in meaning, ranked best-first.

The chunking logic lives in [`doc_chunking.py`](../../src/rust_lsp_mcp/doc_chunking.py)
and the store logic in [`doc_store.py`](../../src/rust_lsp_mcp/doc_store.py).

---

## 7. Refreshing the index

> **Blast radius**: `refresh` restarts the single shared analyzer. All tools return `not_ready` to all clients until re-indexing completes. Avoid calling `refresh` mid-task unless the index is stale or failed.

The `refresh` tool executes two recovery steps:

1. **Restarts rust-analyzer**: The server sets state to `"indexing"` before shutting down the old instance. This prevents race conditions where callers observe `"ready"` during shutdown. The restart runs in the background. Callers should poll `status` until it returns `"ready"`.
2. **Rebuilds documentation search**: The server rebuilds the ChromaDB documentation index synchronously. If the index never initialized or is in an `"error"` state, `refresh` executes the full startup sequence.

`refresh` preserves rust-analyzer's on-disk cache. Re-indexing is significantly faster than a cold start because the analyzer reuses prior build artifacts.

---

## 8. Server lifespan and initialization

The server starts immediately without blocking on background indexing:

- **Code analyzer**: Starts in a background task. The server accepts MCP connections immediately and returns `not_ready` until indexing finishes.
- **Documentation index**:
  - **Warm restart**: If a valid collection exists on disk, the server adopts it on the background worker without re-embedding text. `doc_index_state` reads `"building"` until the adopt completes.
  - **Cold start**: The server builds the index in a background thread. `doc_index_state` reports `"building"` until complete.
  - **Error handling**: Failures are captured and exposed via `status` as `doc_index_state == "error"`. Code navigation tools remain usable.
  - **Chunk count verification**: `status` exposes `doc_index_chunk_count`. A count of `0` helps identify misconfigured Markdown paths or unmounted project directories.

### Startup preflight
On startup, an advisory preflight checks that `rust_analyzer_bin` exists and `project_root` is a valid directory. Preflight issues are recorded in `preflight_warnings` on the `status` tool. Preflight warnings never halt server startup.

When the server stops, it shuts down both rust-analyzer and ChromaDB cleanly. Lifespan logic is implemented in [`core.py`](../../src/rust_lsp_mcp/core.py) and [`analyzer.py`](../../src/rust_lsp_mcp/analyzer.py).

---

## Related pages

- [Components](components.md)
- [Tools / API reference](tools.md)
- [Configuration](configuration.md)
