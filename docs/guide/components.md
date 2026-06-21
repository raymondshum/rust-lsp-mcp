[← Back to the README](../../README.md) · [Documentation index](index.md)

# Components — source code tour

The code lives under `src/rust_lsp_mcp/`. The server is small and organized so
that each tool is its own file and the shared machinery lives in a few core
modules. A request enters through a tool, passes a readiness check, reaches
either rust-analyzer or the documentation search, and comes back as a small
status-tagged result.

## Module responsibilities at a glance

| Module | One-line responsibility |
|---|---|
| `__init__.py`, `_main.py`, `__main__.py`, `server.py` | Entry points — two equivalent launch paths lead here |
| `core.py` | App instance, readiness gate, shared translation helpers |
| `analyzer.py` | rust-analyzer lifecycle: start, state tracking, LSP delegate methods, restart |
| `positions.py` | Converts 1-indexed (human) ↔ 0-indexed (protocol) positions |
| `envelope.py` | Builds the `ok` / `not_ready` / `not_found` / `error` response objects |
| `settings.py` | All configuration options with defaults, loaded from `.env` or environment |
| `doc_chunking.py` | Splits Markdown into embeddable pieces for documentation search |
| `doc_store.py` | ChromaDB-backed documentation search store |
| `tools/` | One file per MCP tool; importing the package registers them all |

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
object from `core.py`. The `main()` function simply calls `mcp.run()`, which
starts the FastMCP server over stdio.

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
`init_doc_store` to build the documentation index. If the doc store fails,
the error is logged and swallowed — navigation tools continue to work normally.
On shutdown both are cleaned up in reverse order.

**The readiness gate.** `require_ready()` is a single check that every
navigation tool calls before doing anything. If rust-analyzer is still indexing
(or hasn't started), it returns a `not_ready` envelope immediately. If the
analyzer is ready it returns `None` and the tool proceeds. The pattern in every
tool file looks like:

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
field plus any additional payload fields. `envelope.py` provides the four
builder functions that produce these objects:

- `ok(**kwargs)` — success; merges any extra fields into the result.
- `not_ready(message=...)` — the analyzer is still indexing; caller should retry.
- `not_found(message=...)` — the symbol or position was not found.
- `error(message)` — something went wrong; the message explains what.

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
- `RLM_DOC_COLLECTION` — name of the ChromaDB collection holding the doc index
  (default: `project_docs`)
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

**`DocStore`** wraps a ChromaDB `PersistentClient`. At startup, `init_doc_store`
checks whether a complete collection already exists on disk. If it does (and has
the completion marker described below), the store adopts it without re-embedding
— the full build can take a while, and bind-mounted storage survives container
rebuilds. If no usable collection exists, `rebuild()` is called.

**`rebuild()`** drops and recreates the ChromaDB collection, globs all
Markdown files matching `doc_glob_patterns` (minus any matching
`doc_exclude_patterns`), chunks each file with `chunk_markdown`, and adds all
chunks to the collection in batches of 500. Before flipping `is_ready` to
`True`, it writes a `build_complete` sentinel into the collection's metadata.
This sentinel is the key to safe adoption: a collection that has documents but
no sentinel was killed mid-build and is treated as incomplete — it is rebuilt
rather than adopted.

**Readiness flag.** `is_ready` is `False` while a build is in progress and
`True` only after the build and sentinel write both complete. The `search_docs`
tool gates on this flag, so a caller never receives a partial or misleading
result during a rebuild.

**`search(query, n_results=5)`** queries the collection and returns up to
`n_results` matches, each as a dict with `file`, `breadcrumb`, `text`, and
`distance` (cosine distance: 0 = identical, lower = more similar). Results are
ordered best-first.

**Module-level singleton.** The live `DocStore` is held in a module-level
variable, set by `init_doc_store` and cleared by `clear_doc_store` on shutdown.
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
| [`status.py`](../../src/rust_lsp_mcp/tools/status.py) | `status` — full 4-field status report (state, indexed commit, current commit, stale flag) |
| [`diagnostics.py`](../../src/rust_lsp_mcp/tools/diagnostics.py) | `analyzer_status` (minimal one-field readiness check) and `probe` (gated no-op for testing the readiness gate) |
| [`refresh.py`](../../src/rust_lsp_mcp/tools/refresh.py) | `refresh` — tear down the analyzer and doc store and rebuild both from scratch |

See the [Tools / API reference](tools.md) for inputs, outputs, and examples for
each tool.

---

## Related pages

- [Architecture](architecture.md) — the big picture: how a request flows through
  the system, key design ideas, and the readiness model.
- [Tools / API reference](tools.md) — every tool, its inputs, and the exact
  responses it returns.
- [Dependencies](dependencies.md) — the main libraries and external tools the
  project relies on, and why.
