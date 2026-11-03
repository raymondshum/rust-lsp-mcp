---
okf_version: "0.1"
type: Guide
title: Code Navigation Tools
description: Detailed reference for find_symbol, goto_definition, find_references, hover, and document_symbols.
tags: [guide, tier-a, tools, navigation]
timestamp: 2026-07-07T00:00:00Z
source_pins:
  - path: src/rust_lsp_mcp/tools/find_symbol.py
    commit: b13c90f6e0dd0301198e3f2c324d956a4a14d74a
  - path: src/rust_lsp_mcp/tools/goto_definition.py
    commit: b13c90f6e0dd0301198e3f2c324d956a4a14d74a
  - path: src/rust_lsp_mcp/tools/find_references.py
    commit: b13c90f6e0dd0301198e3f2c324d956a4a14d74a
  - path: src/rust_lsp_mcp/tools/hover.py
    commit: b13c90f6e0dd0301198e3f2c324d956a4a14d74a
  - path: src/rust_lsp_mcp/tools/document_symbols.py
    commit: b13c90f6e0dd0301198e3f2c324d956a4a14d74a
---

[← Back to Tool Reference](../tools.md) · [Documentation index](../index.md)

# Code Navigation Tools

These tools query rust-analyzer through the Language Server Protocol (LSP) to provide semantic code intelligence.

All navigation tools:
- Require rust-analyzer to be in the `"ready"` state (see [Response Format](../tools.md#response-format)).
- Use 1-based line and character positions (`character` offsets count Unicode codepoints).
- Use paths relative to the project root (e.g. `"src/main.rs"`).

---

## `find_symbol`

Find Rust symbols by name across the workspace. Supports fuzzy prefix matches.

### Inputs

| Name | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Symbol name or prefix to match. |

### Returns

| Status | Payload fields |
|---|---|
| `ok` | `results`: list of symbol objects, `total`: integer, `truncated`: boolean. |
| `not_found` | `message`: confirmation that zero symbols matched the query. |
| `not_ready` | `message`, `recovery: "poll_status"` — analyzer is indexing. |
| `error` | `message`, `recovery: "unknown"` — internal LSP failure. |

#### Symbol Object
```json
{
  "name": "parse_args",
  "kind": "Function",
  "file": "src/args.rs",
  "line": 12,
  "character": 1,
  "container": "args"
}
```

---

## `goto_definition`

Locate where a symbol is defined, given a source location.

### Inputs

| Name | Type | Required | Description |
|---|---|---|---|
| `file` | string | yes | Project-relative path (e.g. `"src/main.rs"`). |
| `line` | integer | yes | 1-based line number. |
| `character` | integer | yes | 1-based character offset (codepoint). |

### Returns

| Status | Payload fields |
|---|---|
| `ok` | `definitions`: list of target location objects. |
| `not_found` | `message`: no definition exists at this location. |
| `not_ready` | `message`, `recovery: "poll_status"` — analyzer indexing. |
| `error` | `message`, `recovery`: `"fix_input"` (path escape) or `"unknown"`. |

---

## `find_references`

Locate all references to the symbol at the given position.

### Inputs

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `file` | string | yes | — | Project-relative path. |
| `line` | integer | yes | — | 1-based line number. |
| `character` | integer | yes | — | 1-based character offset. |
| `include_declaration` | boolean | no | `false` | Include declaration site in results. |
| `include_source` | boolean | no | `false` | Include 1-line source code snippet for each reference. |

### Returns

| Status | Payload fields |
|---|---|
| `ok` | `references`: list of reference locations, `total`, `truncated`. |
| `not_found` | `message`: no symbol at position. |
| `not_ready` | `message`, `recovery: "poll_status"`. |
| `error` | `message`, `recovery`. |

---

## `hover`

Inspect type signatures and documentation for the symbol at the specified position.

### Inputs

| Name | Type | Required | Description |
|---|---|---|---|
| `file` | string | yes | Project-relative path. |
| `line` | integer | yes | 1-based line number. |
| `character` | integer | yes | 1-based character offset. |

### Returns

| Status | Payload fields |
|---|---|
| `ok` | `contents`: Markdown-formatted hover text containing type and doc comments. |
| `not_found` | `message`: no hover information at position. |
| `not_ready` | `message`, `recovery: "poll_status"`. |
| `error` | `message`, `recovery`. |

---

## `document_symbols`

List all symbols declared in a specific file in declaration order.

### Inputs

| Name | Type | Required | Description |
|---|---|---|---|
| `file` | string | yes | Project-relative path (e.g. `"src/main.rs"`). |

### Returns

| Status | Payload fields |
|---|---|
| `ok` | `symbols`: list of declared symbols, `total`, `truncated`. |
| `not_ready` | `message`, `recovery: "poll_status"`. |
| `error` | `message`, `recovery: "fix_input"` (invalid path) or `"unknown"`. |
