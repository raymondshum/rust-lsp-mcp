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
| `refresh` | The analyzer or documentation index has **permanently** failed (`state`/`doc_index_state` == `"error"`), or a doc-index rebuild failed mid-`refresh`. | Call the `refresh` tool (see below), then retry once `status` reports readiness. |
| `poll_status` | The server is transiently not ready (still indexing/building). This is the *only* value `not_ready` ever carries. | Poll `status` or `analyzer_status` and retry once ready. |
| `unknown` | An unexpected/unclassified failure (e.g. the catch-all LSP-exception fallback), or a rare server-side condition that isn't a client input mistake. The default for `error()` when no more specific hint applies. | Surface the message; retrying once is reasonable, but there is no guaranteed fix. |

There is deliberately no `retriable` boolean and no separate `code` enum — see
`docs/audit/2026-07-02-usability-review.md` (UR-6) for why those were
considered and dropped.

### List caps: `total` and `truncated`

`find_symbol`, `document_symbols`, and `find_references` always add two fields
to their `ok` envelope, alongside their list:

| Field | Type | Meaning |
|---|---|---|
| `total` | integer | The full result count, *before* any cap is applied. `total == 0` is how you tell "the query succeeded with zero results" apart from a bad query — no separate flag or message is needed for that case. |
| `truncated` | boolean | `true` only when `total` exceeds `MAX_LIST_RESULTS` (200), in which case the list carries just the first 200 entries, in the same order they'd otherwise appear. `false` (list is complete) otherwise. |

There is deliberately **no pagination/offset** — narrowing the query (a more
specific position, name, or file) is the intended way to get past the cap, not
paging through it. See `docs/audit/2026-07-02-usability-review.md` (UR-11,
which also folds in UR-5 and UR-12) for why pagination was considered and
dropped in favor of this single high safety cap.

---

## Navigation tools

### `find_symbol`

Find Rust symbols by name. Partial names and prefixes work — rust-analyzer
performs a fuzzy match across the whole workspace.

**Inputs**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `name` | string | yes | Symbol name or prefix to search for. |

**Returns**

| Status | Fields |
|---|---|
| `ok` | `results`: list of symbol objects (see below); `total` and `truncated` (see [List caps](#list-caps-total-and-truncated) above). |
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
| `file` | string | yes | Path relative to the project root, e.g. `"src/main.rs"`. Checked lexically before the analyzer is called — absolute paths and `..`-escaping paths are rejected. The check is purely textual (it does not resolve symlinks), so a symlink *inside* the workspace that points outside it is not caught. |

**Returns**

| Status | Fields |
|---|---|
| `ok` | `symbols`: list of symbol objects (see below); `total` and `truncated` (see [List caps](#list-caps-total-and-truncated) above). The list may be empty — a file with only comments or macros is a valid result with zero symbols (`total: 0`). |
| `not_ready` | `message`, `recovery: "poll_status"` — the analyzer is still indexing. |
| `error` | `message`, `recovery` — the language server raised an exception for this request (`recovery: "unknown"`; for example, the file path does not exist in the project or cannot be read), or `file` is an absolute path or escapes the project root via `..` (`recovery: "fix_input"`; rejected before the analyzer is called). Note: a valid file with no symbols returns `ok` with an empty list, not `error`. |

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
| `ok` | `results`: list of chunk objects, best match first (see below). |
| `not_ready` | `message`, `recovery: "poll_status"` — the documentation index is still building (transient — the initial build, or a `refresh`-triggered rebuild, is in flight). Do not interpret this as "no matching docs". Retry once the index is ready (poll `status`, or wait for `refresh` to return). |
| `not_found` | `message` — the index is ready and the collection is genuinely empty. |
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
| `analyzer_error` | string or `null` | Diagnostic message when `state` is `"error"`, else `null`. |
| `indexed_commit` | string or `null` | The git commit hash that was current when indexing started. `null` if not yet captured or git is unavailable. |
| `current_commit` | string or `null` | The git commit hash right now. `null` on any failure (non-git directory, git not installed, etc.). |
| `stale` | boolean or `null` | `true` if the two commit hashes differ; `false` if they match; `null` if either hash is unknown. |
| `doc_index_state` | `"building"`, `"ready"`, or `"error"` | Whether the documentation search index (used by `search_docs`) has finished building. Independent of `state` above. |
| `doc_index_error` | string or `null` | Diagnostic message when `doc_index_state` is `"error"`, else `null`. |

**Caution on `stale`:** the comparison looks at committed versions only. If you
have uncommitted edits in your working tree, `stale` will still be `false`. It
means "no committed changes since indexing began," not a freshness guarantee.

**Example response**

```json
{
  "status": "ok",
  "state": "ready",
  "analyzer_error": null,
  "indexed_commit": "a3f1c9d",
  "current_commit": "a3f1c9d",
  "stale": false,
  "doc_index_state": "ready",
  "doc_index_error": null
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

Rebuild everything from scratch: restart the code analyzer and rebuild the
documentation index. This is also the **recovery path** for either index
having failed permanently (`state == "error"` on the analyzer,
`doc_index_state == "error"` on the doc index) — a fresh `refresh` clears
both failures and starts over.

**Blast radius:** this tears down and re-indexes the single global analyzer
shared by the whole server. Every other tool returns `not_ready` for every
caller until re-indexing completes, which can take minutes on large projects.
Do not invoke `refresh` mid-task unless the index is actually stale or broken
— check `status`/`analyzer_status` first.

**Inputs:** none.

**Returns**

| Status | Fields |
|---|---|
| `ok` | `state`: `"indexing"` (the code re-index has started in the background); `message`: a polling hint. |
| `error` | `message`, `recovery` — one of two cases: (1) the analyzer is not running, in which case nothing is started (`recovery: "unknown"` — a rare startup-ordering condition); or (2) the documentation rebuild/re-init failed, in which case the code re-index has *already* started and continues in the background (`recovery: "refresh"` — fix the doc-index cause, e.g. the embedding model or Chroma path, then call `refresh` again). |

After `refresh` returns `ok`, the code analyzer continues indexing in the
background. Poll `status` or `analyzer_status` until `state` is `"ready"`
before issuing navigation queries.

**Doc-index recovery detail:** if the documentation index was never
initialised, or is currently in the `"error"` state, `refresh` re-runs its
full startup sequence (construct + adopt-or-build) rather than calling
rebuild on a store that may not exist or is known-broken. Otherwise (a
healthy index already present) it rebuilds that index in place. Either way
the doc-index half completes before `refresh` returns.

**Note on speed:** `refresh` does not wipe rust-analyzer's saved on-disk cargo
cache. Only the in-process LSP context is torn down and respawned. Re-indexing
is therefore fast — rust-analyzer reuses its prior work.

**Example response**

```json
{
  "status": "ok",
  "state": "indexing",
  "message": "Re-index started; poll status until state is 'ready'."
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

## Related pages

- [Architecture](architecture.md)
- [Configuration](configuration.md)
