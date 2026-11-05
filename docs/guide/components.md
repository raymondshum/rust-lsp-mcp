---
okf_version: "0.1"
type: Guide
title: Components — source code tour
description: Module-by-module tour of the rust-lsp-mcp server and the rust-lsp CLI client source.
tags: [guide, tier-b, components]
timestamp: 2026-11-04T00:00:00Z
source_pins:
  - path: src/rust_lsp_mcp/core.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/analyzer.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/server.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/envelope.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/positions.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/settings.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/doc_store.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/doc_chunking.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/_main.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/__main__.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/__init__.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/tools/find_symbol.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/tools/goto_definition.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/tools/find_references.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/tools/hover.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/tools/document_symbols.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/tools/search_docs.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/tools/status.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/tools/refresh.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/tools/diagnostics.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/tools/validate_file_path.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_cli/cli.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_cli/client.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
---

[← Back to the README](../../README.md) · [Documentation index](index.md)

# Components — source code tour

The server lives under `src/rust_lsp_mcp/`; the CLI client that talks to it
lives in the sibling package `src/rust_lsp_cli/`. The server is small and
organized so that each tool is its own file and the shared machinery lives in
a few core modules. A request enters through a tool, passes a readiness
check, reaches either rust-analyzer or the documentation search, and comes
back as a small status-tagged result.

## Subsystems at a glance

The codebase is organized into four core subsystems:

| Subsystem | Modules | Primary Responsibility |
|---|---|---|
| **Server Core & Lifecycle** | `server.py`, `core.py`, `envelope.py`, `settings.py` | FastMCP app setup, stdio/HTTP transport, lifespan management, response envelopes, configuration. |
| **Language Server Integration** | `analyzer.py`, `positions.py` | rust-analyzer subprocess lifecycle, background indexing, error recovery, 1-based coordinate translation. |
| **Documentation Intelligence** | `doc_store.py`, `doc_chunking.py` | ChromaDB vector store lifecycle, Markdown chunking with heading breadcrumbs, semantic search. |
| **Tool & CLI Surface** | `tools/`, `rust_lsp_cli/` | Self-registering MCP tool endpoints and the companion `rust-lsp` CLI client for shell environments. |

---

## Entry points

**Files:** [`__init__.py`](../../src/rust_lsp_mcp/__init__.py) · [`_main.py`](../../src/rust_lsp_mcp/_main.py) · [`__main__.py`](../../src/rust_lsp_mcp/__main__.py) · [`server.py`](../../src/rust_lsp_mcp/server.py)

There are two equivalent ways to start the server:

- `uv run rust-lsp-mcp` — uses the `[project.scripts]` console-script entry
  defined in `pyproject.toml`, which calls `main()` from `rust_lsp_mcp`.
- `python -m rust_lsp_mcp` — Python runs `__main__.py`, which calls the same
  `main()`.

Both paths end at `_main.py`, which re-exports `main` from `server.py`.
`server.py` does two things at import time: it imports `rust_lsp_mcp.tools`
(which triggers auto-discovery of every tool file) and imports the `mcp` app
object from `core.py`. `main()` branches on `settings.transport`: the default
`"stdio"` calls `mcp.run()`, which starts the FastMCP server over stdio (one
process per client). `"streamable-http"` instead runs `_run_streamable_http()`,
which starts a warm HTTP daemon on `127.0.0.1` (host is hard-coded, never
settings-derived) that the `rust-lsp` CLI client talks to — see
[CLI reference](cli.md). This daemon+client shape replaced an earlier
warm-start pattern of running a second stdio server inside the same
container; two servers against one Chroma store was a cross-process
single-writer hazard. There is exactly one server process per container.

`server.py` also re-exports `find_symbol` to support a legacy import path used
by integration tests; new code should import tools from `rust_lsp_mcp.tools.*`
directly.

---

## core.py

**File:** [`core.py`](../../src/rust_lsp_mcp/core.py)

`core.py` is the heart of the server. It owns:

**The app object.** The `mcp` variable is the `FastMCP` instance the entire
server runs on (FastMCP is the ready-made server class provided by the `mcp`
package). All tool modules import `mcp` from here and register themselves
by decorating functions with `@mcp.tool()`.

**Lifespan wiring.** The `_lifespan` async context manager is passed to
`FastMCP` at construction. When the server boots it calls `analyzer_lifespan`
(from `analyzer.py`) to start rust-analyzer in the background, then calls
`init_doc_store_background`, which schedules the doc store's lock wait, adopt
check and any build on a worker thread and returns at once. If the doc store
fails, the error is logged and swallowed — navigation tools continue to work
normally. On shutdown both are cleaned up in reverse order.

**The readiness gate.** `require_ready()` is a single check that every
navigation tool calls before doing anything. If rust-analyzer's background run
has permanently failed (`state == "error"`), it returns an `error` envelope
with `recovery="refresh"`. If it is still indexing (or hasn't started), it
returns a `not_ready` envelope. If the analyzer is ready it returns `None` and
the tool proceeds. The pattern in every tool file looks like:

```python
if (guard := require_ready()) is not None:
    return guard
# ... proceed with analyzer call ...
```

**The manager accessor.** `get_manager()` returns the live `AnalyzerManager`
singleton (or `None` if not started). Tools that have already called
`require_ready()` successfully are guaranteed that `get_manager()` returns a
non-`None`, ready manager.

**Shared translation helpers.** Rather than duplicate conversion logic across
tool files, `core.py` provides three helpers that all navigation tools reuse:

- `kind_name(kind_raw)` — converts the raw integer "kind" code that rust-analyzer
  returns (e.g. `12`) into a readable name (e.g. `"Function"`). Uses the
  `SymbolKind` enum from multilspy; unknown integers are returned as their
  string form.
- `location_to_external(loc, repo_root)` — converts an LSP `Location` dict
  (which uses 0-indexed positions and `file://` URIs) into the external shape
  the tools return (1-indexed positions, workspace-relative path). Prefers
  `relativePath` if present; falls back to deriving the path from the `uri`
  field.
- `symbol_to_external(sym, repo_root, default_file)` — converts a full symbol
  info dict (from either a workspace-symbol or document-symbol query) into the
  external representation, handling both shapes multilspy can return.

**Path containment.** `validate_workspace_file(file)` rejects a client-supplied
`file` argument that is empty, absolute, or `..`-escapes the workspace root
before the analyzer delegate is ever called, and returns the normalized path
tools must forward. Every position-taking tool and `validate_file_path` call
it first.

**Tools auto-discovery.** Each file in `tools/` registers itself by calling
`@mcp.tool()` at import time. No central list is maintained; adding a new tool
file is enough.

---

## analyzer.py

**File:** [`analyzer.py`](../../src/rust_lsp_mcp/analyzer.py)

`analyzer.py` manages rust-analyzer's entire lifecycle. It contains two classes
and a lifespan context manager.

**`PatchedRustAnalyzer`** is a small subclass of multilspy's `RustAnalyzer`.
multilspy normally downloads its own rust-analyzer binary from a download table,
but that table has no entry for the linux-arm64 container and pins a stale 2023
build. `PatchedRustAnalyzer` overrides the single method
(`setup_runtime_dependencies`) that controls which binary is used, returning the
path to the container's own installed rust-analyzer binary instead. This is the
only customization — everything else in multilspy is used as-is.

**`AnalyzerManager`** manages the lifecycle. At startup, `start()` spawns a
background task (using `asyncio`, Python's built-in library for running work
concurrently) that enters multilspy's `start_server()` context
(which starts the analyzer process and waits for it to finish indexing the
workspace). Only after `start_server()` yields does the manager flip its `state`
from `"indexing"` to `"ready"` and set the `_lsp` reference. This means the
`is_ready` property is `True` exactly when both conditions hold: `state ==
"ready"` and `_lsp` is set. The two-condition check closes the teardown window
where the background task has already cleared `_lsp` but `state` hasn't been
reset yet.

If the background task raises before reaching quiescence, `state` becomes
`"error"` and `error_message` carries a diagnostic string; `state` stays
`"error"` until `restart()` is called (the `refresh` tool's recovery path),
which clears the error and resets `state` to `"indexing"`.

The manager exposes five **delegate methods** that tools call instead of
accessing multilspy directly:

- `request_workspace_symbol(query)` — find symbols by name across the workspace
- `request_document_symbols(relative_file_path)` — list all symbols in one file
- `request_definition(relative_file_path, line, column)` — go to definition
- `request_references(relative_file_path, line, column)` — find all callers
- `request_hover(relative_file_path, line, column)` — get hover documentation

All positions passed to these methods are 0-indexed (the conversion from the
user-facing 1-indexed numbers happens in the tool layer before the call).

Each delegate guards against `_lsp` being `None` and raises `RuntimeError` if
called before the manager is ready — this is a defensive check, because
`require_ready()` in `core.py` should have already blocked the call.

**Null-response handling.** multilspy 0.0.15 has a quirk: when rust-analyzer
returns JSON-RPC `null` for a definition or references query (meaning "nothing
at this position"), multilspy raises `AssertionError` rather than returning
`None`. The manager catches this specific assertion error in
`request_definition` and `request_references` and normalizes it to `None` (so
the tool layer can map it to a `not_found` response). A real protocol failure —
where the assertion message does not end with `"None"` — is not caught and
propagates as an error. The helper `_is_null_response_assertion` implements
this check.

**`restart()`** tears down the running analyzer and starts a fresh one. It sets
`state = "indexing"` as its very first action — before anything else happens —
so callers can never observe a stale `"ready"` during the re-index window. It
then drains the old background task cleanly, creates fresh event objects (the
old ones are spent and cannot be reused), and calls `start()` to spawn a new
background task.

**`analyzer_lifespan`** is an async context manager that `core.py` uses to wire
the manager into FastMCP's lifespan protocol.

---

## positions.py

**File:** [`positions.py`](../../src/rust_lsp_mcp/positions.py)

This is the single place in the codebase where line and character numbers are
converted between the two counting conventions:

- **External (MCP):** 1-indexed — what users and editors see. Line 1 is the
  first line; character 1 is the first character.
- **Internal (LSP):** 0-indexed — what the Language Server Protocol uses.
  Line 0 is the first line; character 0 is the first character.

Two named-tuple types (`ExternalPosition` and `LspPosition`) make it obvious at
a glance which convention a value uses.

Two functions do the conversion:

- `lsp_to_external(lsp_line, lsp_character)` — adds 1 to each. Used when
  translating positions coming out of rust-analyzer before returning them to
  callers.
- `external_to_lsp(ext_line, ext_character)` — subtracts 1 from each. Used
  when translating user-supplied positions before passing them into rust-analyzer.

Only line and character are converted here. File paths and all other fields are
not touched.

---

## envelope.py

**File:** [`envelope.py`](../../src/rust_lsp_mcp/envelope.py)

Every tool returns the same kind of object: a plain Python dict with a `status`
field plus any additional payload fields. `envelope.py` provides the builder
functions that produce these objects:

- `ok(**kwargs)` — success; merges any extra fields into the result.
- `not_ready(message=...)` — the analyzer is still indexing; caller should
  retry. Always carries `recovery="poll_status"`.
- `not_found(message=...)` — the symbol or position was not found.
- `error(message, recovery="unknown")` — something went wrong; the message
  explains what, and `recovery` is a machine-readable hint at the next action
  (`"fix_input"` / `"refresh"` / `"poll_status"` / `"unknown"`).
- `lsp_failure(exc, context="")` — a thin wrapper over `error()` for the
  catch-all unexpected-LSP-exception case shared by every nav tool; always
  passes `recovery="unknown"`.

The `recovery` field is additive-only: no key renames, no new `status` values,
and deliberately no `retriable` boolean or separate `code` enum (see
`docs/audit/2026-07-02-usability-review.md`, UR-6).

The distinction between `not_found` and `ok` with an empty payload is
intentional: `not_found` means the resolution step itself failed (the symbol
query found no matches, or the position has no symbol at all). `ok` with an
empty list means analysis succeeded but produced a legitimately empty answer —
for example, `find_references` returning zero callers for a real symbol.

These statuses are returned as data inside the MCP response body, not as
protocol-level errors. Protocol-level errors are reserved for genuine crashes
that prevent the tool from running at all.

---

## settings.py

**File:** [`settings.py`](../../src/rust_lsp_mcp/settings.py)

`settings.py` defines all runtime configuration in a single `Settings` class
built on `pydantic-settings`. Every field has a sensible default (pointing at
the devcontainer bind-mount paths) so the server starts with no configuration
required.

To override any setting, set an environment variable prefixed with `RLM_`, or
add a line to a `.env` file in the working directory. The server loads the
`.env` file itself at startup — no external loader is needed.

The key settings are:

- `RLM_PROJECT_ROOT` — path to the Rust workspace to analyze (default:
  `/workspaces/ripgrep`). The older name `RLM_RIPGREP_SRC` still works as a
  deprecated alias (it emits a warning).
- `RLM_RUST_ANALYZER_BIN` — path to the rust-analyzer binary (default:
  `/usr/local/cargo/bin/rust-analyzer`)
- `RLM_CHROMA_PATH` — path for the ChromaDB persistent storage (default:
  `/workspaces/chroma`)
- `RLM_DOC_COLLECTION` — prefix of the ChromaDB collection name holding the doc
  index (default: `project_docs`). The live name is
  `<prefix>_<12 hex characters of the SHA-256 of the project identity>`, where
  the identity is `RLM_PROJECT_ID` when set, else a value derived from the
  project path, its git remote and its root package name, so projects that
  share one Chroma path get separate collections.
- `RLM_DOC_GLOB_PATTERNS` — comma-separated globs selecting Markdown files to
  index (default: `**/*.md`)
- `RLM_DOC_EXCLUDE_PATTERNS` — comma-separated globs to exclude from the index
  (default: `**/CHANGELOG.md`)

See the [Configuration page](configuration.md) for the full list with details.

---

## doc_chunking.py

**File:** [`doc_chunking.py`](../../src/rust_lsp_mcp/doc_chunking.py)

`doc_chunking.py` converts a Markdown file into a list of small, self-contained
`DocChunk` objects that can be fed to an embedding model.

**Why chunking is needed.** Embedding models have a fixed input window (the
MiniLM model used here has a 256-token limit). A documentation page is usually
much longer than that. Chunking breaks it into pieces small enough to embed
without silent truncation.

**What a `DocChunk` contains.** Each chunk has four fields:

- `id` — a stable unique identifier in the form `"rel_path::ordinal"`.
- `text` — the string that gets embedded: `"breadcrumb\n\nbody"`.
- `file` — the workspace-relative path to the source file.
- `breadcrumb` — the ancestor heading chain for this section, e.g.
  `"GUIDE.md > Configuration > Ignoring files"`. This gives the embedding
  model context about where in the document the chunk comes from.

**How chunking works.** The public function `chunk_markdown(text, rel_path)` runs a two-stage split:

1. **Header-tree split.** The document is split on headings. Both ATX headings
   (`# Title`) and setext headings (a line of `===` or `---` under a text line)
   are recognized. Lines inside fenced code blocks (`` ``` `` or `~~~`) are
   never treated as headings. Each section gets a breadcrumb built from its
   ancestor headings. The text before the first heading (the preamble) gets a
   breadcrumb equal to the filename alone.

2. **Size split.** Any section whose text (breadcrumb plus body) would exceed
   200 estimated tokens is split further. The split uses a three-level cascade:
   first it tries to accumulate whole paragraphs (blank-line-separated), carrying
   one paragraph of overlap into the next piece for semantic continuity. If a
   single paragraph is still too large, it falls back to splitting on line
   boundaries. If a single line is still too large (e.g. a very long URL or
   minified text), it splits by word or character as a last resort. This cascade
   guarantees that every chunk fits the model's input window regardless of the
   input content.

**Token estimation.** Rather than running a real tokenizer, `doc_chunking.py`
uses a conservative heuristic that takes the larger of two estimates: a
character-based estimate (`non-CJK characters / 2`, with CJK characters counted
as 1 token each) and a word-based estimate (`words × 1.5`). Both
intentionally over-estimate so that the real token count is never larger than
the estimated cap.

---

## doc_store.py

**File:** [`doc_store.py`](../../src/rust_lsp_mcp/doc_store.py)

`doc_store.py` implements the documentation search store. It uses ChromaDB with
cosine-distance similarity search to find the chunks most relevant to a natural-
language query.

**`DocStore`** wraps a ChromaDB `PersistentClient`. At startup, one exclusive
flock on `<chroma_path>/.rlm-doc-store.lock` covers the store's construction,
the adopt check and any rebuild, so two processes starting on one volume take
turns: the second waits, then adopts what the first one built. The adopt check
reuses the on-disk collection without re-embedding only when all of these hold:
it was built for the same project (identity and fingerprint), with the same
build settings (doc globs, exclusions, chunker version and embedding model),
from the same tree (its stamped commit equals the current `HEAD` and its doc
digest equals the current one; when the git call fails, the digest alone
decides), and it carries the `build_complete` sentinel. Otherwise `rebuild()`
runs under the same flock.

**`rebuild()`** builds into a staging collection named `<live name>_staging`.
On a store that has already published a collection (a `refresh` on a running
server), that collection keeps answering searches until the swap. A rebuild at
startup, after the adopt check refused the on-disk collection, serves nothing
until the swap, though it still copies unchanged files from that collection.
It selects the Markdown files
matching `doc_glob_patterns` (minus any matching `doc_exclude_patterns`), copies
the chunks and embeddings of each file whose path and SHA-256 match the live
collection, and chunks and embeds the rest with `chunk_markdown`. It then swaps
the staging collection in: it deletes the live collection and renames staging
to the live name with its metadata, including the `build_complete` sentinel.
The sentinel is the key to safe adoption: a collection without it was killed
mid-build and is rebuilt rather than adopted. A `rebuild()` that `refresh`
calls on a running store takes the same flock.

**Readiness flag.** `is_ready` is `False` until the store publishes its first
collection, by adopt or by build. That covers a first start, and any start or
`refresh` re-initialisation whose adopt check fails, even when an older
collection is on disk. After that, a rebuild keeps it `True`, because the
published collection answers until the swap. The `search_docs` tool gates on this flag, so a caller never
receives a partial or misleading result during a build.

**`search(query, n_results=5)`** queries the collection and returns up to
`n_results` matches, each as a dict with `file`, `breadcrumb`, `text`, and
`distance` (cosine distance: 0 = identical, lower = more similar). Results are
ordered best-first. The `search_docs` tool clamps the caller-supplied `limit`
it passes in as `n_results` to `[1, MAX_DOC_RESULTS]` (currently 50) — a bare
floor-only clamp let an oversized `limit` pull an unbounded number of chunks
into a single response (see `docs/audit/2026-07-02-usability-review.md`,
UR-12).

**Module-level singleton.** The live `DocStore` is held in a module-level
variable, set by `_prepare_store` (which both `init_doc_store` and
`init_doc_store_background` run) and cleared by `clear_doc_store` on shutdown.
Tools call `get_doc_store()` to retrieve it.

---

## tools/

**Package:** [`tools/`](../../src/rust_lsp_mcp/tools/)

The `tools/` directory contains one file per MCP tool. Every file registers its
tool with the FastMCP app by decorating a function with `@mcp.tool()` at module
level.

**Auto-discovery.** When `server.py` imports `rust_lsp_mcp.tools`, the
package's `__init__.py` iterates every non-private submodule with `pkgutil` and
imports each one. This import triggers the `@mcp.tool()` decorators, which
register the tools with the app. Adding a new tool requires nothing more than
dropping a new file in `tools/` — no central registry edit is needed.

**Tool files:**

| File | Tool(s) it registers |
|---|---|
| [`find_symbol.py`](../../src/rust_lsp_mcp/tools/find_symbol.py) | `find_symbol` — search for symbols by name across the workspace |
| [`document_symbols.py`](../../src/rust_lsp_mcp/tools/document_symbols.py) | `document_symbols` — list all symbols defined in a single file |
| [`goto_definition.py`](../../src/rust_lsp_mcp/tools/goto_definition.py) | `goto_definition` — jump to where a symbol at a given position is defined |
| [`find_references.py`](../../src/rust_lsp_mcp/tools/find_references.py) | `find_references` — find all places a symbol at a given position is used |
| [`hover.py`](../../src/rust_lsp_mcp/tools/hover.py) | `hover` — get the documentation and type signature at a given position |
| [`search_docs.py`](../../src/rust_lsp_mcp/tools/search_docs.py) | `search_docs` — semantic search over the indexed Markdown documentation |
| [`status.py`](../../src/rust_lsp_mcp/tools/status.py) | `status` — full status report: analyzer state/staleness (indexed vs. current commit), doc-index state/chunk count, and advisory startup preflight warnings |
| [`diagnostics.py`](../../src/rust_lsp_mcp/tools/diagnostics.py) | `analyzer_status` (minimal one-field readiness check) and `probe` (gated no-op for testing the readiness gate) |
| [`refresh.py`](../../src/rust_lsp_mcp/tools/refresh.py) | `refresh` — restart the analyzer and rebuild the doc index in the background (build-and-swap; unchanged files keep their embeddings) |
| [`validate_file_path.py`](../../src/rust_lsp_mcp/tools/validate_file_path.py) | `validate_file_path` — check whether a workspace path exists and report its absolute path and size; an analyzer-free filesystem probe, ungated |

See the [Tools / API reference](tools.md) for inputs, outputs, and examples for
each tool.

---

## rust_lsp_cli/ — the CLI client

**Files:** [`cli.py`](../../src/rust_lsp_cli/cli.py) · [`client.py`](../../src/rust_lsp_cli/client.py)

`rust-lsp` is the command-line client that talks to a warm streamable-HTTP
daemon (a `server.py` process started with `RLM_TRANSPORT=streamable-http`).
It runs no server of its own.

**`cli.py`** builds the argparse surface: one subcommand per navigation tool,
plus `search-docs`, `status`, `refresh`, `validate-file-path`, and `version`. A `COMMANDS`
table maps each kebab-case subcommand to an MCP tool name and an
argument-builder function; the same table backs a parity test that checks it
against `mcp.list_tools()`. Only stdlib imports (`argparse`/`json`/`os`/`sys`)
run at module level — `rust_lsp_cli.client` is imported lazily inside `main()`
so `--help` and argparse-only invocations stay fast, and this package never
imports `rust_lsp_mcp` at all (that would pull in the full server, including a
second Chroma client). Exit codes are exact: `0` ok/not_found, `1` error, `2`
not_ready, `3` daemon unreachable (`EXIT_OK` / `EXIT_ERROR` /
`EXIT_NOT_READY` / `EXIT_UNREACHABLE`). `--wait SECS` polls `status` every 2s
until the daemon reports both `state == "ready"` and
`doc_index_state == "ready"`, riding through daemon boot
(connection-refused → not_ready → ready); a `doc_index_state` of `"error"`
ends the wait at once.

**`client.py`** is the streamable-HTTP MCP client half: `call_tool_sync` opens
one connection per call, calls a tool, and parses the envelope from
`content[0].text` (falling back to `structuredContent`), returning
`(envelope, None)` on success or `(None, exc)` for any transport-level failure
(connection refused, handshake failure, timeout) — never raises. `wait_for_ready`
implements the `--wait` polling loop.

See the [CLI reference](cli.md) for every subcommand, its arguments, and exit-code
examples.

---

## Related pages

- [Architecture](architecture.md) — the big picture: how a request flows through
  the system, key design ideas, and the readiness model.
- [Tools / API reference](tools.md) — every tool, its inputs, and the exact
  responses it returns.
- [CLI reference](cli.md) — every `rust-lsp` subcommand, its arguments, and exit codes.
- [Dependencies](dependencies.md) — the main libraries and external tools the
  project relies on, and why.
