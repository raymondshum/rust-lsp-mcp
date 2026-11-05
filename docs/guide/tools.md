---
okf_version: "0.1"
type: Guide
title: Tool reference
description: Every MCP tool the server exposes, with its inputs and exact envelope responses.
tags: [guide, tier-a, tools]
timestamp: 2026-11-04T00:00:00Z
source_pins:
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
  - path: src/rust_lsp_mcp/envelope.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
---

[← Back to the README](../../README.md) · [Documentation index](index.md)

# Tool reference

This page documents every MCP tool exposed by the service. Each tool accepts
JSON arguments and returns a JSON object.

Several tools reach rust-analyzer through the Language Server Protocol (LSP) —
the standard interface that code editors use to ask a language engine questions
like "where is this defined?" Failures coming from that layer are reported with
the `error` status, described below.

Every tool also advertises MCP `ToolAnnotations` describing its side effects:
all tools are `readOnlyHint: true` except `refresh`, which is
`readOnlyHint: false`, `destructiveHint: true`, `idempotentHint: false` (see
its blast-radius note below).

---

## Response format

Every tool returns an object with a `status` field. The four possible values are:

| Status | Meaning |
|---|---|
| `ok` | The query ran successfully. The payload may be empty — an empty result is a meaningful answer, not an error. |
| `not_ready` | The code analyzer is still indexing (or the documentation index is building). Transient — wait and retry. Always includes a `message` field and a `recovery` field (always `"poll_status"`). |
| `not_found` | The requested thing does not exist at that name or position. Always includes a `message` field. |
| `error` | Bad input, an internal failure, *or* a **permanently** failed analyzer/doc index (`state`/`doc_index_state` == `"error"` on the `status` tool). Unlike `not_ready`, this does not resolve on its own — call the `refresh` tool to recover. Always includes a `message` field and a `recovery` field. |

**Positions are 1-based.** The first character of a file is line 1, character 1.
A `character` offset counts **Unicode codepoints** (the intuitive "Nth
character"), so positions stay correct on lines containing non-ASCII text. File
paths are relative to the project root (e.g. `"src/main.rs"`).

### The `recovery` field

Every `not_ready` and `error` envelope carries an additive `recovery` field —
a single machine-readable hint at the next action, so a caller can branch on
it directly instead of parsing `message` text. Exactly four values:

| `recovery` | Meaning | Typical next action |
|---|---|---|
| `fix_input` | The request itself was malformed (bad position, bad path, empty query). Retrying unchanged will fail identically. | Correct the argument(s) and call again. |
| `refresh` | The analyzer or documentation index has **permanently** failed (`state`/`doc_index_state` == `"error"`), or the doc-index rebuild thread could not be started. | Call the `refresh` tool (see below), then retry once `status` reports readiness. |
| `poll_status` | The server is transiently not ready (still indexing/building). This is the *only* value `not_ready` ever carries. | Poll `status` or `analyzer_status` and retry once ready. |
| `unknown` | An unexpected/unclassified failure (e.g. the catch-all LSP-exception fallback), or a rare server-side condition that isn't a client input mistake. The default for `error()` when no more specific hint applies. | Surface the message; retrying once is reasonable, but there is no guaranteed fix. |

The envelope provides the single `recovery` field rather than separate boolean flags or nested error codes. Callers can branch directly on the recovery hint.

### List caps: `total` and `truncated`

`find_symbol`, `document_symbols`, and `find_references` always add two fields
to their `ok` envelope, alongside their list:

| Field | Type | Meaning |
|---|---|---|
| `total` | integer | Total matching results before capping. `total: 0` confirms a successful search with zero matches. |
| `truncated` | boolean | `true` when results exceed the 200-item safety cap (`MAX_LIST_RESULTS`). `false` when the list contains all matches. |

For `find_symbol`, rust-analyzer itself stops at 201 matches (one past the cap). `total` counts the in-workspace matches (the items rust-analyzer returned, minus dependency and standard-library symbols the tool drops), and `truncated` is `true` when rust-analyzer returned more than 200 items before that filter. A truncated `total` is therefore a lower bound: at most 201, and below 200 when some returned items were outside the project.

Tools enforce a flat safety cap without pagination. To inspect results beyond the 200-item cap, refine your query with a narrower symbol prefix, specific filename, or declaration filter.

---

## Tool directory

The server exposes 9 tools categorized into three functional domains:

| Category | Tools | Focus | Detailed Guide |
|---|---|---|---|
| **Code Navigation** | `find_symbol`, `goto_definition`, `find_references`, `hover`, `document_symbols` | LSP-backed semantic code intelligence | [Navigation Reference](tools/navigation.md) |
| **Documentation Search** | `search_docs` | Local ChromaDB vector search over Markdown | [Search Reference](tools/search.md) |
| **Administration** | `status`, `refresh`, `validate_file_path` | Server health, cache invalidation, input checks | [Admin Reference](tools/admin.md) |

---

## Navigation tools

### `find_symbol`

Find Rust symbols by name. Partial names and prefixes work — rust-analyzer
performs a fuzzy match across the whole workspace. Results cover every symbol
kind: functions and methods as well as structs, enums, traits, constants and
modules.

**Inputs**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `name` | string | yes | Symbol name or prefix to search for. |

**Returns**

| Status | Fields |
|---|---|
| `ok` | `results`: list of symbol objects (see below), at most 200; `total` (in-workspace matches before the cap) and `truncated` (`true` when rust-analyzer had more than 200 matches; see [List caps](#list-caps-total-and-truncated) above). |
| `not_found` | `message` — no symbols matched the query, or rust-analyzer returned nothing. |
| `not_ready` | `message`, `recovery: "poll_status"` — the analyzer is still indexing. |
| `error` | `message`, `recovery: "unknown"` — unexpected failure from the LSP layer. |

Each item in `results`:

```json
{
  "name":      "parse_args",
  "kind":      "Function",
  "file":      "src/args.rs",
  "line":      12,
  "character": 1,
  "container": "args"
}
```

- `kind` is a human-readable label such as `"Function"`, `"Struct"`, `"Enum"`,
  `"Method"`, `"Module"`, etc.
- `container` is the enclosing module or type name, or `null` when there is none.
- Multiple matches are normal. The caller picks the right one by inspecting
  `kind`, `container`, and `file`.

**Example response**

```json
{
  "status": "ok",
  "results": [
    {
      "name": "parse_args",
      "kind": "Function",
      "file": "src/args.rs",
      "line": 12,
      "character": 1,
      "container": null
    },
    {
      "name": "parse_args_strict",
      "kind": "Function",
      "file": "src/args.rs",
      "line": 34,
      "character": 1,
      "container": null
    }
  ],
  "total": 2,
  "truncated": false
}
```

---

### `document_symbols`

List every symbol defined in one file, in declaration order.

**Inputs**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `file` | string | yes | Project-relative path (e.g. `"src/main.rs"`). Absolute paths or paths with `..` are rejected immediately. |

**Returns**

| Status | Fields |
|---|---|
| `ok` | `symbols`: list of symbol objects; `total` and `truncated`. Valid files with zero declarations return an empty list (`total: 0`). |
| `not_ready` | `message`, `recovery: "poll_status"` — the analyzer is still indexing. |
| `error` | `message`, `recovery` — returns `recovery: "fix_input"` for invalid paths, or `recovery: "unknown"` for LSP read failures. |

Each item in `symbols` (there is no `file` field — all entries belong to the
file you passed in):

```json
{
  "name":      "Config",
  "kind":      "Struct",
  "line":      8,
  "character": 1,
  "container": null,
  "detail":    "struct Config"
}
```

`container` is almost always `null` for document symbols — rust-analyzer rarely
populates the enclosing-scope field in this response.

`detail` is **optional** — rust-analyzer's signature for the symbol (e.g. a
function's parameter/return types), included only when rust-analyzer supplies
a non-empty value. It is omitted entirely (never emitted as `null`) when
absent, so an outline of a large file does not pay for a redundant key on
every entry. `detail` is specific to `document_symbols`; `find_symbol` does not
carry it (workspace-symbol results have no equivalent field).

`line`/`character` point at the symbol's **name**, not the start of the full
declaration — so a symbol preceded by doc comments or `#[attributes]` still
resolves to the name itself. This makes the position directly usable as input
to `hover`, `goto_definition`, or `find_references`. (Falls back to the start
of the declaration only if a provider omits the name-position data.)

**Example response**

```json
{
  "status": "ok",
  "symbols": [
    { "name": "Config",  "kind": "Struct",   "line": 8,  "character": 1, "container": null, "detail": "struct Config" },
    { "name": "new",     "kind": "Method",   "line": 15, "character": 5, "container": null, "detail": "fn new() -> Self" },
    { "name": "run",     "kind": "Function", "line": 42, "character": 1, "container": null }
  ],
  "total": 3,
  "truncated": false
}
```

---

### `goto_definition`

Find where the item at a given position is defined.

**Inputs**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `file` | string | yes | Path relative to the project root. Checked lexically before the analyzer is called — absolute paths and `..`-escaping paths are rejected. The check is purely textual (it does not resolve symlinks), so a symlink *inside* the workspace that points outside it is not caught. |
| `line` | integer | yes | 1-based line number of the cursor position. |
| `character` | integer | yes | 1-based character offset of the cursor position. |

**Returns**

| Status | Fields |
|---|---|
| `ok` | `definitions`: list of location objects (see below). More than one is possible, e.g. a trait method that has several implementations. |
| `not_found` | `message` — no definition was found at that position (the position holds no symbol — whitespace, comment, or unknown token — or every returned location fell outside the project root, e.g. a standard-library or dependency definition, and was skipped). |
| `not_ready` | `message`, `recovery: "poll_status"` — the analyzer is still indexing. |
| `error` | `message`, `recovery` — `line` or `character` is less than 1, or `file` is an absolute path or escapes the project root via `..` (both rejected before the analyzer is called; `recovery: "fix_input"`), or an unexpected LSP failure (`recovery: "unknown"`). |

Each item in `definitions`:

```json
{
  "file":      "src/config.rs",
  "line":      8,
  "character": 1
}
```

Every entry in `definitions` is guaranteed to be inside the project root.
Definitions that resolve outside it (e.g. into the Rust standard library or a
crates.io dependency) are silently skipped rather than returned with a
`..`-prefixed path; if every candidate is skipped the tool returns
`not_found`.

**Example response**

```json
{
  "status": "ok",
  "definitions": [
    { "file": "src/config.rs", "line": 8, "character": 1 }
  ]
}
```

---

### `find_references`

Find all uses of the item at a given position.

**Inputs**

| Name | Type | Default | Meaning |
|---|---|---|---|
| `file` | string | required | Path relative to the project root. Checked lexically before the analyzer is called — absolute paths and `..`-escaping paths are rejected. The check is purely textual (it does not resolve symlinks), so a symlink *inside* the workspace that points outside it is not caught. |
| `line` | integer | required | 1-based line number. |
| `character` | integer | required | 1-based character offset. |
| `include_declaration` | boolean | `false` | When `true`, also include the definition site in the results (merged and deduplicated). |
| `include_source` | boolean | `false` | When `true`, each reference gains a `source` field with the trimmed text of its source line, read from the file at response time (point-in-time snapshot; may lag the index if files changed since indexing). |

**Returns**

| Status | Fields |
|---|---|
| `ok` | `references`: list of location objects; `total` and `truncated` (see [List caps](#list-caps-total-and-truncated) above). The list may be empty — see note below (`total: 0`). References that resolve outside the project root (e.g. into the standard library) are silently skipped. |
| `not_found` | `message` — no symbol at that position (whitespace, comment, or unknown token). |
| `not_ready` | `message`, `recovery: "poll_status"` — the analyzer is still indexing. |
| `error` | `message`, `recovery` — `line` or `character` is less than 1, or `file` is an absolute path or escapes the project root via `..` (both rejected before the analyzer is called; `recovery: "fix_input"`), or an unexpected failure (`recovery: "unknown"`). |

Each item in `references`:

```json
{
  "file":      "src/main.rs",
  "line":      27,
  "character": 9
}
```

When `include_source=true`, each item additionally carries:

```json
{
  "file":      "src/main.rs",
  "line":      27,
  "character": 9,
  "source":    "let cfg = parse_args(&raw_input)?;"
}
```

`source` is the stripped text of that line, truncated to 300 characters, read
directly from the file on disk **at response time** — not from the analyzer's
index. This is a point-in-time snapshot: if the file changed since the last
index run, `source` reflects the current on-disk content, which may disagree
with the (possibly stale) position the analyzer reported. `source` is `null`
for any hit whose file or line could not be read (missing file, permission
error, out-of-range line) — this never causes the whole call to fail. The
cap (see [List caps](#list-caps-total-and-truncated) above) is applied
*before* enrichment, so at most 200 lines are ever read for a single call. The
default `include_source=false` leaves the payload byte-for-byte the same
shape as before (aside from the always-present `total`/`truncated` fields).

**Important: `ok` + empty list is not the same as `not_found`.**

- `ok` with an empty `references` list (`total: 0`) means the symbol is real
  but has no callers in the indexed workspace. This is a meaningful answer
  (e.g. a dead function or an internal item with no in-tree users).
- `not_found` means there is no symbol at that position at all — the cursor is
  on whitespace, a comment, or an unknown token.

**Example response (real symbol, zero callers)**

```json
{
  "status": "ok",
  "references": [],
  "total": 0,
  "truncated": false
}
```

**Example response (symbol with callers)**

```json
{
  "status": "ok",
  "references": [
    { "file": "src/main.rs", "line": 27, "character": 9 },
    { "file": "tests/smoke.rs", "line": 14, "character": 5 }
  ],
  "total": 2,
  "truncated": false
}
```

---

### `hover`

Show the type signature and documentation for the item at a given position.

**Inputs**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `file` | string | yes | Path relative to the project root. Checked lexically before the analyzer is called — absolute paths and `..`-escaping paths are rejected. The check is purely textual (it does not resolve symlinks), so a symlink *inside* the workspace that points outside it is not caught. |
| `line` | integer | yes | 1-based line number. |
| `character` | integer | yes | 1-based character offset. |

**Returns**

| Status | Fields |
|---|---|
| `ok` | `contents`: a Markdown string containing the type signature and any rustdoc comments, exactly as rust-analyzer produces them. |
| `not_found` | `message` — nothing to show at that position (whitespace, comment, or unsupported construct). |
| `not_ready` | `message`, `recovery: "poll_status"` — the analyzer is still indexing. |
| `error` | `message`, `recovery` — `line` or `character` is less than 1, or `file` is an absolute path or escapes the project root via `..` (both rejected before the analyzer is called; `recovery: "fix_input"`), or an unexpected LSP failure (`recovery: "unknown"`). |

**Example response**

```json
{
  "status": "ok",
  "contents": "```rust\nfn parse_args(input: &str) -> Result<Config, Error>\n```\n\nParse a command-line argument string into a `Config`."
}
```

---

## Documentation search

### `search_docs`

Meaning-based search over the project's Markdown documentation. Unlike a
keyword search, this finds content that is _about_ your question even when the
exact words do not appear.

**Inputs**

| Name | Type | Default | Meaning |
|---|---|---|---|
| `query` | string | required | A natural-language question or topic. |
| `limit` | integer | `5` | Maximum number of results to return. Clamped to between 1 and 50 (`MAX_DOC_RESULTS`) inclusive — a request for more than 50 is silently reduced to 50 rather than dumping an unbounded number of chunks in one response. There is no `truncated` flag here: unlike the LSP list tools, semantic (top-k nearest-neighbour) search has no meaningful "more results exist" signal to report — narrow the query for different results rather than requesting a larger `limit`. |

**Returns**

| Status | Fields |
|---|---|
| `ok` | `results`: list of chunk objects, best match first (see below). `doc_stale`: `true` when a Markdown file the index covers was added, removed or changed since the index was built (the results may be out of date; `refresh` rebuilds it), `false` when none was, `null` when unknown. Same value as `status.doc_stale`. |
| `not_ready` | `message`, `recovery: "poll_status"` — the documentation index is still building (transient — the first build on an empty store is in flight; a `refresh`-triggered rebuild over an existing index does not cause this, because the old index keeps answering). Do not interpret this as "no matching docs". Retry once `status` reports `doc_index_state: "ready"`. |
| `not_found` | `message`, `doc_stale` — the index is ready and the collection is genuinely empty. `doc_stale` is as for `ok`. |
| `error` | `message`, `recovery` — either (a) `query` is empty or whitespace-only (rejected immediately, before any readiness check or search — no index round-trip; `recovery: "fix_input"`), (b) the documentation index **failed to build or initialise** (permanent until recovered — unlike `not_ready`, retrying immediately will not help; the message includes the underlying reason; `recovery: "refresh"`), or (c) an unexpected failure from the search layer itself (`recovery: "unknown"`). For (b), call `refresh` to rebuild the index and clear the failure. |

`not_ready` and `error` mean different things here: `not_ready` is transient
(the index is actively being built and will become `ready` on its own),
while `error` is permanent until you call `refresh` (the build already
finished, unsuccessfully). The `doc_index_state` field on the `status` tool
distinguishes the same three states (`"building"` / `"ready"` / `"error"`)
independently of the code analyzer's own readiness.

Each item in `results`:

```json
{
  "file":       "docs/guide/configuration.md",
  "breadcrumb": "configuration.md > Server settings > Port",
  "text":       "The `port` setting controls which TCP port the server binds to...",
  "distance":   0.18
}
```

- `breadcrumb` is the heading trail through the document, e.g.
  `"GUIDE.md > Configuration"`.
- `distance` is the cosine distance between the query and the chunk in
  embedding space. Smaller means closer (more relevant). `0` would be an exact
  match; typical useful matches are below `0.4`.

**Example response**

```json
{
  "status": "ok",
  "results": [
    {
      "file":       "docs/guide/configuration.md",
      "breadcrumb": "configuration.md > Server settings",
      "text":       "Set RLM_PROJECT_ROOT to the absolute path of the Rust project to navigate...",
      "distance":   0.21
    }
  ]
}
```

---

## Operational / status tools

### `status`

Full status of the code analyzer and the documentation index. Always callable
— always returns `ok` at the envelope level, even when the analyzer or doc
index has failed (failures are reported as *field values*, not as the
envelope status). Use this to decide whether to issue navigation queries or
`search_docs` calls, and to detect stale indexes.

**Inputs:** none.

**Returns**

Always `ok`, with these fields:

| Field | Type | Meaning |
|---|---|---|
| `state` | `"indexing"`, `"ready"`, or `"error"` | Whether the analyzer has finished indexing. `"error"` means the background indexing run failed permanently — gated tools (via `require_ready`) return an `error` envelope (`recovery: "refresh"`, not `not_ready`) while in this state. Call `refresh` to retry. |
| `analyzer_error` | string or `null` | Diagnostic message when `state` is `"error"`, else `null`. After a rust-analyzer crash it reads `rust-analyzer exited with code <n>; last stderr: <up to 20 lines>`. |
| `analyzer_health` | `"ok"`, `"warning"`, `"error"`, or `null` | rust-analyzer's own health report from its latest `experimental/serverStatus` notification. `"error"` (for example a failed workspace load) can appear while `state` is `"ready"`: queries still run, over an incomplete workspace. `null` before the first report, after `refresh` and after rust-analyzer exits (`state` is then `"error"`). |
| `analyzer_health_message` | string or `null` | The message of that health report, or `null`. |
| `watcher_health` | `"ok"`, `"error"`, or `null` | Health of rust-analyzer's file watcher. `"error"` once rust-analyzer's stderr reports a watch failure in this run (for example the host's inotify watch limit, `notify error: OS file watch limit reached.`): edits and branch switches may no longer reach the code index until `refresh`. `"ok"` once `state` is `"ready"` with no such failure. `null` before the run is ready unless a watch failure was already seen in this run (then `"error"`), after `refresh` and after rust-analyzer exits. |
| `watcher_message` | string or `null` | The rust-analyzer stderr line that reported the first watch failure of the run, or `null`. |
| `analyzer_exit_code` | integer or `null` | Exit code of a rust-analyzer process that exited without a shutdown request; `state` is then `"error"` and gated tools return `rust-analyzer is not running (exited with code <n>); call refresh` with `recovery: "refresh"`. `null` otherwise; cleared by `refresh`. |
| `indexed_commit` | string or `null` | The git commit hash captured at analyzer start, re-captured once rust-analyzer reports itself quiescent after a re-index, and re-captured by `status` when HEAD moved over a tree the index already reflects (see `stale`). `null` if not yet captured or git is unavailable. |
| `current_commit` | string or `null` | The git commit hash right now. `null` on any failure (non-git directory, git not installed, etc.). |
| `stale` | boolean or `null` | `true` if the two commit hashes differ; `false` if they match; `null` if either hash is unknown. The server watches the project tree, so after a branch switch or pull that makes rust-analyzer re-index, `indexed_commit` is re-captured once rust-analyzer reports itself quiescent. After a commit of saved edits, or a switch that starts no re-index (one that changes no Rust or Cargo file included), `status` re-captures it itself once rust-analyzer is quiescent and every Rust or Cargo file that differs between the two commits matches HEAD on disk and was last modified more than 2 seconds ago. Either way `stale` returns to `false` without `refresh`. `status` makes no such re-capture while `watcher_health` is `"error"`: with a failed watcher an edit on disk may never have reached the index, so `stale` keeps reading `true` until `refresh`. |
| `indexing_started_at` | string or `null` | ISO 8601 UTC time the current analyzer indexing run began; reset by every `refresh`. `null` before the first run. |
| `analyzer_progress` | string or `null` | The latest rust-analyzer progress title and message (for example `"Indexing 120/512 (serde)"`). After that work ends it reads `"<title> done"` (for example `"Indexing done"`), or `null` when the work had no title. `null` before the first progress report of the run. |
| `analyzer_progress_at` | string or `null` | ISO 8601 UTC time of that progress report, or `null`. |
| `doc_index_state` | `"building"`, `"ready"`, or `"error"` | Whether the documentation search index (used by `search_docs`) can answer. Independent of `state` above. Stays `"ready"` while a rebuild runs over an existing index, because `search_docs` keeps answering from the old index; reads `"building"` for the first build on an empty store, and for an errored index while a `refresh` rebuild of it is in flight (`doc_index_error` then reads `null`, and `rust-lsp --wait` keeps polling). |
| `doc_index_error` | string or `null` | Diagnostic message when `doc_index_state` is `"error"`. Also set while `doc_index_state` is `"ready"` when the last rebuild failed and the old index is still being served. `null` otherwise. |
| `doc_index_progress` | object or `null` | Progress of a running doc index rebuild, `null` when none is running: `files_total`, `files_done`, `started_at` (ISO 8601 UTC) and `phase` (`"starting"` while it waits for its locks, then `"scanning"`, `"embedding"` and `"swapping"`). |
| `doc_index_chunk_count` | integer or `null` | Number of indexed Markdown chunks (a live `collection.count()`), or `null` when `doc_index_state != "ready"`. `0` is a valid, meaningful reading — a completed index over zero matching Markdown files (e.g. wrong `RLM_DOC_GLOB_PATTERNS`, empty/un-mounted project, or a project that genuinely ships no docs) — and is distinct from `null` ("not ready yet / no store"). Works whether the index was just rebuilt or adopted from a prior run. |
| `doc_indexed_commit` | string or `null` | The git commit hash stamped on the doc index when it was built (`"none"` if git failed then). Informational: `doc_stale` does not compare it. `null` when `doc_index_state != "ready"`. |
| `doc_stale` | boolean or `null` | `true` if a Markdown file the index covers was added, removed or changed (size or modification time) since the build; `false` if none was, including after a branch switch or commit that leaves the doc files byte-identical; `null` when `doc_index_state != "ready"`. The doc files are the only input. No running server rebuilds the doc index on its own: this stays `true` until the next `refresh` or server restart. |
| `doc_unreadable_files` | integer or `null` | Number of matched Markdown files the build could not read (for example a permission error). A non-zero count means that many matched files are absent from the index. `0` when every file was read; `null` when `doc_index_state != "ready"` or the index was built before this count existed. |
| `preflight_warnings` | array of strings | Advisory-only diagnostics computed once at server startup: whether the configured `rust_analyzer_bin` resolves to an existing executable, and whether `project_root` exists and is a directory. An empty array means either "both checks passed" or "the preflight has not run yet" (e.g. some test contexts) — these are intentionally indistinguishable. Warnings here never affect `state`, `doc_index_state`, or the envelope `status` — they are a hint, not a failure signal. |
| `server_version` | string or `null` | Installed `rust-lsp-mcp` package version, resolved fresh on every call. `null` if the distribution metadata isn't resolvable. |
| `multilspy_version` | string or `null` | Installed `multilspy` package version, same resolution and `null`-degradation as `server_version`. |
| `rust_analyzer_version` | string or `null` | The analyzer binary's `--version` output (e.g. `"rust-analyzer 0.3.x (<sha> <date>)"`), captured once when the analyzer manager starts and cached for the process lifetime — not re-run on every `status` call. `null` before a manager exists, or if the binary was missing, failed, or timed out at capture time. |

**Caution on `stale`:** the comparison looks at committed versions only. If you
have uncommitted edits in your working tree, `stale` will still be `false`. It
means "no committed changes since indexing began," not a freshness guarantee.

**Example response**

```json
{
  "status": "ok",
  "state": "ready",
  "analyzer_error": null,
  "analyzer_health": "ok",
  "analyzer_health_message": null,
  "analyzer_exit_code": null,
  "watcher_health": "ok",
  "watcher_message": null,
  "indexed_commit": "a3f1c9d",
  "current_commit": "a3f1c9d",
  "stale": false,
  "indexing_started_at": "2026-11-04T07:28:38+00:00",
  "analyzer_progress": "Indexing done",
  "analyzer_progress_at": "2026-11-04T07:28:59+00:00",
  "doc_index_state": "ready",
  "doc_index_error": null,
  "doc_index_progress": null,
  "doc_index_chunk_count": 42,
  "doc_indexed_commit": "a3f1c9d",
  "doc_stale": false,
  "doc_unreadable_files": 0,
  "preflight_warnings": [],
  "server_version": "0.4.0",
  "multilspy_version": "0.0.15",
  "rust_analyzer_version": "rust-analyzer 0.3.2145-standalone (a1b2c3d 2026-06-01)"
}
```

---

### `analyzer_status`

A minimal readiness check. Always callable — never returns `not_ready`. Use
this for a lightweight poll to know when it is safe to call navigation tools.
For the full report (`analyzer_error`, commit hashes, staleness, doc-index
state), use `status` instead.

**Inputs:** none.

**Returns**

Always `ok`, with one field:

| Field | Type | Meaning |
|---|---|---|
| `state` | `"indexing"`, `"ready"`, or `"error"` | Whether the analyzer has finished indexing. `"error"` means the background indexing run failed permanently (see `status` for the diagnostic message); gated tools return `error`, not `not_ready`, in that state. |

**Example response**

```json
{
  "status": "ok",
  "state": "ready"
}
```

---

### `refresh`

Restart the code analyzer process and rebuild the documentation index in the
background. This is also the **recovery path** for either index having failed
permanently (`state == "error"` on the analyzer, `doc_index_state == "error"`
on the doc index) — a fresh `refresh` clears both failures and starts over.

**Blast radius:** this tears down and re-indexes the single global analyzer
shared by the whole server. Every navigation tool returns `not_ready` for
every caller until re-indexing completes, which can take minutes on large
projects. `search_docs` keeps answering from the old doc index while the doc
rebuild runs. Do not invoke `refresh` mid-task unless the index is actually
stale or broken — check `status`/`analyzer_status` first.

**Inputs:** none.

**Returns**

| Status | Fields |
|---|---|
| `ok` | `state`: `"indexing"` (the code re-index has started in the background); `doc_index_state`: the doc index state at return (`"ready"` while the old index is served during the rebuild); `doc_rebuild_started`: `true` when this call started the doc rebuild, `false` when a doc build or rebuild was already running (none is queued); `doc_index_progress`: the running rebuild's progress, or `null`; `message`: a polling hint. |
| `error` | `message`, `recovery` — one of two cases: (1) the analyzer is not running, in which case nothing is started (`recovery: "unknown"` — a rare startup-ordering condition); or (2) the doc rebuild thread could not be started, in which case the code re-index has *already* started and continues in the background (`recovery: "refresh"`). |

After `refresh` returns `ok`, the code analyzer continues indexing in the
background. Poll `status` or `analyzer_status` until `state` is `"ready"`
before issuing navigation queries.

**What happens:** the rust-analyzer process restarts and re-reads the
workspace, and the doc index rebuilds in the background. `refresh` returns
once the doc rebuild thread has started; it does not wait for the rebuild.
The rebuild writes a staging collection and swaps it in when it succeeds, so
`search_docs` serves the old index meanwhile. Unchanged Markdown files keep
their stored embeddings; a changed or renamed file is re-embedded, and every
file is re-embedded when there is no complete live index for this project or
the chunker version or the embedding model changed. Follow the rebuild through `status`'s
`doc_index_progress`.

**Doc-index recovery detail:** if the documentation index was never
initialised, or is currently in the `"error"` state, the rebuild worker
re-runs the full startup sequence (construct + adopt-or-build) rather than
calling rebuild on a store that may not exist or is known-broken. Otherwise
(a healthy index already present) it rebuilds that index in place. A doc
rebuild failure cannot appear in the `refresh` envelope, because `refresh`
has already returned: `status` reports it in `doc_index_error`, and
`doc_index_state` reads `"error"` only when no old index is left to serve.

**Example response**

```json
{
  "status": "ok",
  "state": "indexing",
  "doc_index_state": "ready",
  "doc_rebuild_started": true,
  "doc_index_progress": null,
  "message": "Re-index started; poll status until state is 'ready'. The doc index rebuilds in the background (status.doc_index_progress); search_docs keeps answering from the old index meanwhile."
}
```

---

### `probe`

**Diagnostic only.** Confirms the readiness gate works end-to-end. Returns
`not_ready` (`recovery: "poll_status"`) while the analyzer is indexing, `error`
(`recovery: "refresh"`) if the analyzer's background run failed
(`state == "error"`), and `ok` with a short message once it is ready. This
tool has no everyday use; it exists to verify the gating mechanism during
development and testing.

---

### `validate_file_path`

Check whether a workspace-relative path exists, and report its absolute path
and size. A lightweight filesystem probe that never touches rust-analyzer, so
it is **ungated**: it works even while the analyzer is still indexing.

**Inputs**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `file` | string | yes | Workspace-relative path to probe, e.g. `"src/main.rs"`. |

**Returns**

| Status | Fields |
|---|---|
| `ok` | `exists` (bool), `absolute_path` (str), `size_bytes` (int or `null`, populated only for a regular file). `exists: false` is a valid answer, not an error. |
| `error` | `message`, `recovery` — the workspace root is unconfigured (`recovery: "unknown"`, a server-side misconfiguration), or `file` fails the workspace containment rule (`recovery: "fix_input"`). |

Containment rule: identical to the position tools (absolute paths, even ones
pointing inside the workspace, and `..`-escaping paths are rejected). The
check is purely lexical, and the *normalized* path is what gets probed, so a
symlink combined with `..` cannot resolve outside the workspace.

**Example response**

```json
{
  "status": "ok",
  "exists": true,
  "absolute_path": "/project/src/main.rs",
  "size_bytes": 1423
}
```

---

## Related pages

- [Architecture](architecture.md)
- [Configuration](configuration.md)
