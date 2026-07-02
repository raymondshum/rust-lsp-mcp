[← Back to the README](../../README.md) · [Documentation index](index.md)

# Architecture

This page explains how the rust-lsp-mcp service is put together — what the
pieces are, how they talk to each other, and why a few design choices were
made. No prior knowledge of language servers or AI tooling is required.

---

## 1. The big picture

The service sits between an AI assistant and two information sources:

- **rust-analyzer** — a code-intelligence engine for Rust that can answer
  questions like "where is this function defined?" or "who calls this struct?"
- **A local documentation search** — finds relevant passages in the project's
  Markdown files based on the meaning of a question, not just keywords.

```
AI assistant (MCP client)
        │  (standard input/output)
        ▼
   rust-lsp-mcp server
     ├── rust-analyzer  ── code navigation (definitions, references, hover, symbols)
     └── documentation search ── meaning-based search over the project's Markdown
```

**MCP** stands for Model Context Protocol — it is a standard way for AI
assistants to call external tools, the same way a web browser calls web APIs.
The server speaks MCP over standard input/output: the AI assistant launches
the server as a subprocess and writes requests to it; the server writes
answers back. No network port is needed.

---

## 2. How a request flows

Here is what happens when an AI assistant asks "find all references to this
function":

1. **The client calls the tool** — it sends a request naming the file, line,
   and column of the function.
2. **Input validation** — the server checks that the file path and position
   make sense before doing any real work.
3. **Readiness check** — the server confirms that rust-analyzer has finished
   indexing the codebase (see section 3). If it has not, the server returns
   a `not_ready` status immediately instead of asking the analyzer.
4. **Position conversion** — line and column numbers are converted from the
   1-based counting humans use to the 0-based counting the protocol expects
   (see section 5).
5. **Ask rust-analyzer** — the server passes the request to rust-analyzer
   through a library called **multilspy** (version 0.0.15). multilspy speaks
   the Language Server Protocol (LSP), the standard way editors talk to
   code-intelligence engines like rust-analyzer.
6. **Translate the answer** — the raw LSP response (0-based positions, URI
   paths) is converted back into a clean, 1-based, human-readable result.
7. **Return a status envelope** — the server wraps the result in a small
   object with a `status` field (see section 4) and returns it to the client.

---

## 3. Readiness: never a misleading empty answer

This is the single most important design rule in the service.

When the server starts, rust-analyzer must first **index** the codebase —
read and understand all the Rust source files. This can take several seconds
to a few minutes depending on the project size. A naive system asked "find
all callers of `foo`" during this window would answer "none found" — which
is wrong and looks identical to a real "no callers" result. There is no way
for the caller to tell the difference.

To avoid this trap, the server tracks an explicit readiness state:

- The analyzer starts in the `"indexing"` state.
- It flips to `"ready"` only after rust-analyzer signals that it has finished
  and the live connection is confirmed. In [`analyzer.py`](../../src/rust_lsp_mcp/analyzer.py),
  the `is_ready` property requires both conditions: `state == "ready"` *and*
  a live internal connection object.
- If the background indexing run itself fails (for example, the configured
  rust-analyzer binary does not exist or crashes during startup), the state
  becomes `"error"` instead — a third, permanent-until-recovered state,
  distinct from the transient `"indexing"` window. The diagnostic message is
  captured and exposed as `analyzer_error` on the `status` tool.
- Every tool that needs the index calls `require_ready()` (defined in
  [`core.py`](../../src/rust_lsp_mcp/core.py)) before doing any work. If the
  analyzer is still indexing, `require_ready()` returns a `not_ready`
  envelope immediately and the tool returns that without touching the
  analyzer. If the analyzer is in the `"error"` state, `require_ready()`
  instead returns an `error` envelope (never `not_ready` — a permanent
  failure is not "try again in a moment") naming the failure and pointing at
  the `refresh` tool as the recovery path.

The documentation search has its own separate readiness tri-state (`state` on
`DocStore` in [`doc_store.py`](../../src/rust_lsp_mcp/doc_store.py):
`"building"` / `"ready"` / `"error"`) for the same reason: `search_docs` never
returns partial results while a rebuild is in progress, and surfaces a
permanent build failure as `error` rather than a misleading `not_ready`.

---

## 4. The response format (the "status envelope")

Every tool returns a small object with a `status` field. There are exactly
four possible values, defined in [`envelope.py`](../../src/rust_lsp_mcp/envelope.py):

| Status | Meaning |
|---|---|
| `ok` | The query ran successfully. The result may be empty, and that empty result is meaningful. |
| `not_ready` | The index is still being built. Try again in a moment. |
| `not_found` | The thing asked about does not exist. |
| `error` | Bad input, an internal failure, *or* a permanently failed analyzer/doc index (see §3) — the `refresh` tool is the recovery path in that last case. A human-readable message is included. |

**The distinction between `ok` with an empty list and `not_found` is
deliberate and important.**

- `not_found` means the resolution step itself failed — there is no symbol
  at that position, or no symbol with that name exists. It is a dead end.
- `ok` with an empty list means the query ran successfully and rust-analyzer
  confirmed the symbol exists, but it genuinely has zero results — for
  example, a function that is defined but never called anywhere in the
  codebase. That zero is meaningful information.

Collapsing these two into the same response would make it impossible to
distinguish "I could not find that symbol" from "I found it and it has no
callers."

---

## 5. Counting from 1, not 0

Humans and most editors count lines and columns starting from 1: "line 5,
column 3." The Language Server Protocol counts from 0: the same position
is "line 4, character 2."

The server always presents 1-based positions to the outside world and always
sends 0-based positions to rust-analyzer internally. The conversion happens
in one small module — [`positions.py`](../../src/rust_lsp_mcp/positions.py) — so
there is a single place to audit and a single place to fix if anything ever
goes wrong. No tool module does its own arithmetic on positions.

---

## 6. How documentation search works

The documentation search finds Markdown passages that are *meaningfully*
close to a question, not just textually similar. Here is the pipeline,
step by step:

**(a) Chunking.** The project's Markdown files are split into small,
self-contained pieces called "chunks." Each chunk is short enough to be
processed in one go (kept under 200 tokens — a deliberate, conservative cap
that stays well within the embedding model's 256-token input limit, so no text
is ever silently cut off). Each chunk is labeled with a
breadcrumb that records its position in the document hierarchy — for example,
`GUIDE.md > Configuration > Ignoring files`.

**(b) Embedding.** Each chunk is converted into a list of numbers called an
"embedding." The numbers capture the meaning of the text in a way that can
be compared mathematically. This is done by a small model called
**all-MiniLM-L6-v2**, which runs entirely on the local CPU using the ONNX
(Open Neural Network Exchange) runtime — a standard format for running
machine-learning models efficiently. No external service or internet
connection is needed.

**(c) Storage.** The chunks and their embeddings are stored in a local
database called **ChromaDB**, configured to use cosine distance as its
similarity measure. Cosine distance is a way of comparing two embeddings
where a value of 0 means "identical meaning" and larger values mean "less
similar." The database can quickly find the chunks whose embeddings are
closest to any given query.

**(d) Search.** When a search query arrives, it is embedded the same way.
The database returns the chunks with the smallest cosine distance to the
query — the closest in meaning — ranked best-first.

The chunking logic lives in [`doc_chunking.py`](../../src/rust_lsp_mcp/doc_chunking.py)
and the store logic in [`doc_store.py`](../../src/rust_lsp_mcp/doc_store.py).

---

## 7. Refreshing the index

The `refresh` tool rebuilds everything from scratch:

1. It restarts the code analyzer. The state is set back to `"indexing"`
   *before* the old analyzer is shut down, so there is never a window where
   a caller sees `"ready"` but the old index is being torn down. The restart
   runs in the background — the tool returns immediately with an `"indexing"`
   status, and callers should poll the `status` tool until it shows `"ready"`.
2. It rebuilds the documentation search index. This runs synchronously and
   finishes before the tool returns, so the documentation search is not in a
   partial state after the refresh tool completes. If the doc index was never
   initialised, or is currently in the `"error"` state, `refresh` re-runs the
   full startup sequence for it (construct + adopt-or-build) rather than
   calling rebuild on a store that may not exist or is known-broken —
   `refresh` is therefore also the recovery path for a permanently-failed doc
   index, the same way it is for the analyzer's `"error"` state (§3).

One deliberate omission: the refresh does **not** delete rust-analyzer's
saved on-disk work (its incremental analysis cache). This means re-indexing
is much faster than a cold start — rust-analyzer can reuse prior work.

---

## 8. Startup and shutdown (lifespan)

When the server starts, it is available immediately — startup never blocks on
either index finishing:

- The analyzer is launched in the background. The server becomes available to
  clients immediately, answering `not_ready` until indexing completes (or
  `error` if the background run itself fails — see §3).
- The documentation index follows the same non-blocking shape, split into a
  cheap synchronous part and a potentially-slow background part:
    - If an already-completed, same-project collection exists on disk (a warm
      restart), it is **adopted synchronously** — a metadata check only, no
      re-embedding — and the index is `"ready"` before the lifespan even
      yields.
    - Otherwise the index is built from scratch in a **background thread**:
      the lifespan yields immediately (`status`/navigation tools are usable
      right away) while `doc_index_state` reports `"building"`; it flips to
      `"ready"` when the background build finishes.
    - A failure in either the cheap or the background half is recorded (not
      swallowed silently) and surfaced via `status` as `doc_index_state ==
      "error"` with a diagnostic message — the code-navigation tools are
      unaffected either way, and `refresh` is the recovery path (§7).

When the server stops, both the code analyzer and the documentation store are
shut down cleanly. The lifespan wiring lives in
[`core.py`](../../src/rust_lsp_mcp/core.py) and
[`analyzer.py`](../../src/rust_lsp_mcp/analyzer.py).

---

## Related pages

- [Components](components.md)
- [Tools / API reference](tools.md)
- [Configuration](configuration.md)
