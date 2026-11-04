---
okf_version: "0.1"
type: Guide
title: Administrative & Diagnostic Tools
description: Detailed reference for status, refresh, and validate_file_path tools.
tags: [guide, tier-a, tools, admin]
timestamp: 2026-07-07T00:00:00Z
source_pins:
  - path: src/rust_lsp_mcp/tools/status.py
    commit: b13c90f6e0dd0301198e3f2c324d956a4a14d74a
  - path: src/rust_lsp_mcp/tools/refresh.py
    commit: b13c90f6e0dd0301198e3f2c324d956a4a14d74a
  - path: src/rust_lsp_mcp/tools/validate_file_path.py
    commit: b13c90f6e0dd0301198e3f2c324d956a4a14d74a
---

[← Back to Tool Reference](../tools.md) · [Documentation index](../index.md)

# Administrative & Diagnostic Tools

These operational tools inspect server health, manage index lifecycles, and validate inputs.

---

## `status`

Inspects analyzer and documentation store readiness, commit freshness, and system versions.
`status` is always callable regardless of current indexing state and never returns `not_ready`.

### Inputs
None.

### Returns
Always returns `status: "ok"` with a health envelope:

| Field | Type | Values / Description |
|---|---|---|
| `state` | string | `"indexing"`, `"ready"`, or `"error"`. |
| `analyzer_error` | string \| null | Diagnostic error string if `state` is `"error"`. After a rust-analyzer crash: `rust-analyzer exited with code <n>; last stderr: <up to 20 lines>`. |
| `analyzer_health` | string \| null | rust-analyzer's own health report: `"ok"`, `"warning"` or `"error"` (for example a failed workspace load). `"error"` can appear while `state` is `"ready"`. `null` before the first report and after `refresh`. |
| `analyzer_health_message` | string \| null | The message of that health report. |
| `analyzer_exit_code` | integer \| null | Exit code of a rust-analyzer process that exited without a shutdown request; `state` is then `"error"` and gated tools return `rust-analyzer is not running (exited with code <n>); call refresh`. Cleared by `refresh`. |
| `indexed_commit` | string \| null | Git HEAD commit captured at analyzer start and re-captured when a re-index completes. |
| `current_commit` | string \| null | Live git HEAD commit on disk. |
| `stale` | boolean \| null | `true` if `indexed_commit != current_commit`. `null` if git is unavailable. `indexed_commit` is re-captured when a watcher-driven re-index ends, so a branch switch that changes Rust or Cargo files returns this to `false` without `refresh`. |
| `indexing_started_at` | string \| null | ISO 8601 UTC time the current indexing run began (reset by `refresh`). |
| `analyzer_progress` | string \| null | Latest rust-analyzer progress title and message. |
| `analyzer_progress_at` | string \| null | ISO 8601 UTC time of the latest progress report. |
| `doc_index_state` | string | `"building"`, `"ready"`, or `"error"`. Stays `"ready"` during a rebuild over an existing index (the old index keeps answering); `"building"` only for the first build on an empty store. |
| `doc_index_error` | string \| null | Error message if the documentation index build failed; also set with `doc_index_state` `"ready"` when the last rebuild failed and the old index is still served. |
| `doc_index_progress` | object \| null | Progress of a running doc index rebuild: `files_total`, `files_done`, `started_at` (ISO 8601 UTC) and `phase` (`"starting"` while it waits for its locks, then `"scanning"`, `"embedding"` and `"swapping"`). `null` when no rebuild is running. |
| `doc_index_chunk_count` | integer \| null | Total stored document chunks (`0` indicates unmounted or empty doc path). |
| `doc_indexed_commit` | string \| null | Git commit stamped on the doc index at build time; `null` unless `doc_index_state` is `"ready"`. |
| `doc_stale` | boolean \| null | `true` if the commit changed, or a Markdown file the index covers was added, removed or changed, since the doc index was built; stays `true` until `refresh` or a restart. `null` unless `doc_index_state` is `"ready"`. |
| `preflight_warnings` | list[string] | Advisory warnings detected during startup. |
| `server_version` | string \| null | Installed package version of `rust-lsp-mcp`. |
| `multilspy_version` | string \| null | Installed version of `multilspy`. |
| `rust_analyzer_version` | string \| null | Captured version string from `rust-analyzer --version`. |

---

## `refresh`

Restarts the language server process and rebuilds the documentation index in the background. It does not wait for either: it returns once the analyzer restart has begun and the doc rebuild thread has started.

> **Blast radius warning**: `refresh` resets the shared language server. Concurrent navigation requests return `not_ready` until re-indexing completes. `search_docs` keeps answering from the old doc index during the doc rebuild. Avoid calling `refresh` during active query workflows unless the index is stale or in an `"error"` state.

The doc rebuild writes a staging collection and swaps it in on success, so the old index is served meanwhile and a failed rebuild loses nothing. Only Markdown files whose content changed are re-embedded. Follow it through `status`'s `doc_index_progress`.

### Inputs
None.

### Returns
- `ok`: `state` (`"indexing"`: the analyzer restart has begun; poll `status` until `state` is `"ready"`), `doc_index_state` as it stands at return (`"ready"` while the old index is served), `doc_rebuild_started` (`true` when this call started the doc rebuild; `false` when a doc build or rebuild was already running, in which case none is queued), `doc_index_progress` (the running rebuild's progress or `null`) and `message`.
- `error`: the analyzer is not running (`recovery: "unknown"`), or the doc rebuild thread could not start (`recovery: "refresh"`; the analyzer restart is already under way). A doc rebuild that fails later is reported by `status` in `doc_index_error`, not by `refresh`.

---

## `validate_file_path`

Checks whether a target file path is contained within the project workspace before dispatching operations.

### Inputs

| Name | Type | Required | Description |
|---|---|---|---|
| `file` | string | yes | Project-relative path to validate. |

### Returns

| Status | Fields |
|---|---|
| `ok` | `file`: string path, `valid`: boolean (`true` if inside workspace, `false` otherwise). |
| `error` | `message`, `recovery: "fix_input"` if argument format is invalid. |
