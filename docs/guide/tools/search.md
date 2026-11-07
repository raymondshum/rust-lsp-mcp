---
okf_version: "0.1"
type: Guide
title: Documentation Search Tool
description: Detailed reference for search_docs semantic search over project Markdown files.
tags: [guide, tier-a, tools, search]
timestamp: 2026-11-05T00:00:00Z
source_pins:
  - path: src/rust_lsp_mcp/tools/search_docs.py
    commit: 47012aa930be0efbbb29ac82b4c9e69b949021d3
  - path: src/rust_lsp_mcp/doc_store.py
    commit: 47012aa930be0efbbb29ac82b4c9e69b949021d3
---

[← Back to Tool Reference](../tools.md) · [Documentation index](../index.md)

# Documentation Search Tool (`search_docs`)

Performs natural-language semantic vector search across the project's Markdown documentation.

The tool uses local embeddings generated via **all-MiniLM-L6-v2** and stored in ChromaDB using cosine distance. It runs entirely offline without calling external APIs.

Results come from the live index. While a `refresh` rebuilds the doc index, the live index is the previous build, so results may be one rebuild behind until the rebuild swaps in its new index (`status` shows `doc_index_progress` meanwhile).

---

## Inputs

| Name | Type | Required | Default | Bounds | Description |
|---|---|---|---|---|---|
| `query` | string | yes | — | non-empty | Natural language question or phrase to search. |
| `limit` | integer | no | `5` | `1` to `50` | Maximum number of chunks to return. |

Empty or whitespace-only queries return `error` with `recovery: "fix_input"`. Out-of-bounds `limit` values are automatically clamped to the `[1, 50]` range.

---

## Returns

| Status | Fields |
|---|---|
| `ok` | `results`: ranked list of matching documentation passages (closest distance first). `doc_stale`: `true` when a Markdown file the index covers was added, removed, or changed in size or modification time since the build, including a checkout that rewrites a covered file with the same bytes (results may be out of date; run `refresh`), `false` when none was, `null` when unknown. |
| `not_ready` | `message`, `recovery: "poll_status"` — the first doc index build on an empty store is running, or a `refresh` is re-initialising an errored index. |
| `not_found` | `message`, `doc_stale` — zero matching passages found, or documentation collection is empty. `doc_stale` is as for `ok`. |
| `error` | `message`, `recovery` — search failure, or a permanent store error (`recovery: "refresh"`): the doc index build or initialisation failed, or another process on the volume deleted its collection. |

### Match Object
```json
{
  "text": "Markdown passage body...",
  "breadcrumb": "GUIDE.md > Configuration > Persistence",
  "file": "docs/guide/configuration.md",
  "distance": 0.284
}
```

- `text`: The chunk body, with the breadcrumb prefix stripped (`""` for a heading with no body).
- `distance`: Cosine distance score (`0.0` represents identical meaning).
- `breadcrumb`: Hierarchical heading trail locating the passage in the source document.
- `file`: Path to the source Markdown document relative to project root.
