---
okf_version: "0.1"
type: Guide
title: CLI reference (rust-lsp)
description: Command-line client reference for rust-lsp -- subcommands, exit codes, and --wait semantics for agents without MCP tool access.
tags: [guide, tier-a, cli]
timestamp: 2026-11-05T00:00:00Z
source_pins:
  - path: src/rust_lsp_cli/cli.py
    commit: 47012aa930be0efbbb29ac82b4c9e69b949021d3
  - path: src/rust_lsp_cli/client.py
    commit: 47012aa930be0efbbb29ac82b4c9e69b949021d3
cite:
  - src/rust_lsp_cli/cli.py:EXIT_NOT_READY
---

[← Back to the README](../../README.md) · [Documentation index](index.md)

# CLI reference (`rust-lsp`)

This page documents `rust-lsp`, a command-line client for the read-only navigation tools.

Use `rust-lsp` when agents run shell commands but cannot call MCP tools directly. If your assistant already supports MCP, use MCP tool calls instead. For daemon startup instructions, see [CLI access](../../README.md#cli-access-for-agents-without-mcp).

`rust-lsp` is a lightweight client. It does not run rust-analyzer or ChromaDB directly. Instead, it sends HTTP requests to a long-lived **daemon** (`rust-lsp-mcp` with `RLM_TRANSPORT=streamable-http`). Response schemas and status codes match the MCP server exactly (see [Tools / API reference](tools.md)).

## Connection details

By default, `rust-lsp` connects to `http://127.0.0.1:${RLM_HTTP_PORT:-8000}/mcp`. To override the destination URL, set `RLM_CLI_URL`. See [Configuration](configuration.md#a-cli-only-variable-rlm_cli_url).

When using the provided Docker compose environment, execute `rust-lsp` inside the daemon container:

```bash
docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp status
```

> **Executable path**: Specify the full path `/app/.venv/bin/rust-lsp`. The container image's default `PATH` does not include the virtual environment directory.

If running inside the development container or an activated virtual environment, you can invoke `rust-lsp` directly without `docker exec`.

## Subcommands

Every subcommand prints one JSON envelope (the same `{"status": ..., ...}`
shape documented per-tool in the [Tools / API reference](tools.md)) to
**stdout**, and any diagnostics — connection errors, usage hints — to
**stderr**. There is no human-readable text format; output is always JSON, so
scripts and agents can parse it directly. File paths are workspace-relative
(e.g. `src/main.rs`), and line/character positions are **1-indexed** — the
first line and first character of a file are both `1`, matching the MCP
tools, not raw LSP's 0-indexed convention.

| Subcommand | MCP tool | Syntax |
|---|---|---|
| `find-symbol` | `find_symbol` | `rust-lsp find-symbol NAME` |
| `goto-definition` | `goto_definition` | `rust-lsp goto-definition FILE LINE CHARACTER` |
| `find-references` | `find_references` | `rust-lsp find-references FILE LINE CHARACTER [--include-declaration] [--include-source]` |
| `hover` | `hover` | `rust-lsp hover FILE LINE CHARACTER` |
| `document-symbols` | `document_symbols` | `rust-lsp document-symbols FILE` |
| `search-docs` | `search_docs` | `rust-lsp search-docs QUERY [--limit N]` |
| `status` | `status` | `rust-lsp status` |
| `refresh` | `refresh` | `rust-lsp refresh` |
| `validate-file-path` | `validate_file_path` | `rust-lsp validate-file-path FILE` |
| `version` | — (client-only, see below) | `rust-lsp version` |
| `mcp-bridge` | — (stdio relay, see [below](#mcp-bridge-stdio-relay-for-rlm-connect)) | `rust-lsp mcp-bridge [--port N] [--listener-timeout SECS] [--log-level LEVEL]` |

Each subcommand's arguments map directly onto its tool's inputs — see the
matching section of the [Tools / API reference](tools.md) for what each field
means and every possible response shape. `--limit` on `search-docs` defaults
to `5` and is clamped server-side to `[1, 50]`, same as the `search_docs`
tool's `limit` argument.

### Examples

Look up a symbol by name:

```
$ docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp find-symbol Config
{
  "status": "ok",
  "results": [
    {
      "name": "Config",
      "kind": "Struct",
      "file": "src/config.rs",
      "line": 12,
      "character": 12,
      "container": null
    }
  ],
  "total": 1,
  "truncated": false
}
```

Check readiness, riding out the daemon's first-boot indexing window:

```
$ docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp --wait 180 status
{
  "status": "ok",
  "state": "ready",
  "analyzer_error": null,
  "indexed_commit": "a3f1c9d",
  "current_commit": "a3f1c9d",
  "stale": false,
  "doc_index_state": "ready",
  "doc_index_error": null,
  "doc_index_chunk_count": 42,
  "preflight_warnings": [],
  "server_version": "0.1.0",
  "multilspy_version": "0.0.15",
  "rust_analyzer_version": "rust-analyzer 1.82.0 (abc1234 2026-06-01)"
}
```

Client and daemon version info:

```
$ docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp version
{
  "client_version": "0.1.0",
  "server_version": "0.1.0",
  "multilspy_version": "0.0.15",
  "rust_analyzer_version": "rust-analyzer 1.82.0 (abc1234 2026-06-01)"
}
```

`version` always exits `0` and always prints `client_version` (read locally
from installed package metadata), even if the daemon is unreachable — it
makes a best-effort `status` call for the daemon fields and degrades them to
`null` (with a stderr note) rather than failing. A version query about the
CLI itself shouldn't require a live daemon.

## Exit codes

Every subcommand other than `version` maps the tool envelope's `status` (or a
connection failure) onto a process exit code, so a shell script or agent can
branch on `$?` without parsing JSON:

| Exit code | Meaning |
|---|---|
| `0` | Envelope `status` was `ok` **or** `not_found`. Both are answers, not failures — `not_found` means "the thing you asked about doesn't exist," which is a fact, not an error, matching the same "empty is not an error" doctrine as the MCP tools (see the [Tools / API reference](tools.md#response-format)). |
| `1` | Envelope `status` was `error`. Check the `message` field in the printed JSON, and `recovery` for a machine-readable hint at the next action. |
| `2` | `not_ready` (`EXIT_NOT_READY`) — the daemon answered but the analyzer or doc index is still (re)building. Also used when a `--wait SECS` window expired while the daemon *was* reachable but never reported `ready`, and when `--wait` on `search-docs` or `status` stopped because the daemon reported a doc index error (`doc_index_state: "error"`; stderr prints the error and says to run `rust-lsp refresh`). `rust-lsp --wait SECS refresh` does not stop there: it runs the refresh instead, and exits with the refresh envelope's code, or `3` when the daemon cannot be reached for the refresh call, or `4` when that call times out. |
| `3` | The daemon could not be reached at all — connection refused, handshake failure, or connect timeout. Also used when a `--wait SECS` window expired without the daemon ever answering. Stderr prints a hint to start the daemon. |
| `4` | `EXIT_TIMEOUT` — the daemon was reached, but the request timed out after 300 s with no reply (the CLI's read timeout). rust-analyzer may be stalled. Stderr prints `rust-lsp: the daemon at <url> was reached, but the request timed out after 300s with no reply; ...`, names `rust-lsp status` (`analyzer_stall`) and `rust-lsp refresh` as the next steps, and prints the innermost exception (for example `ReadTimeout`). The server bounds each rust-analyzer request at `RLM_REQUEST_TIMEOUT_SECONDS` (default 120 s), so this exit appears only when that setting is `0` or at least 300 (at least 150 for `find-references --include-declaration`, which makes two requests, each bounded on its own), or when a call hangs outside rust-analyzer. |
| `2` (argparse) | A command-line usage error (bad or missing arguments, unknown subcommand) also exits `2`, from argparse's own default — this numerically collides with `not_ready` above but is never ambiguous in practice: a usage error happens *before* any network call and prints its own distinct usage message on stderr, whereas `not_ready` always comes with a printed JSON envelope on stdout. |

## `--wait SECS`: riding out daemon boot

`--wait` is a **global** option and must come *before* the subcommand name:

```
rust-lsp --wait 180 status
```

(not `rust-lsp status --wait 180` — argparse's subparser mechanics require
global options ahead of the subcommand).

With `--wait SECS` given, `rust-lsp` polls the daemon's `status` tool every 2
seconds, for up to `SECS` seconds, before running your actual command. For
`search-docs`, `status` and `refresh`, the wait ends only when **both**
indexes are ready: `state == "ready"` (the code index) and `doc_index_state
== "ready"` (the documentation index), so a `search-docs` call right after a
successful wait does not exit `2`. Every other command (the navigation
commands, which never read the doc index) waits for the code index alone:
`state == "ready"` ends the wait whatever `doc_index_state` reads. This
is meant to absorb the daemon's first-use indexing window (which can take
minutes on a large project) in one command instead of a hand-rolled retry
loop. It rides through the *entire* boot sequence, not just the "already
connected but indexing" phase: a daemon that isn't listening yet moves
through connection-refused → `not_ready` → `ready`, and `--wait` treats a
connection failure exactly like `not_ready` **as long as the window hasn't
expired** — it keeps polling either way.

A doc index in `"error"` stays failed until `refresh`, so `--wait` on
`search-docs` or `status` stops at the first `status` reply that reports
`doc_index_state: "error"`, without waiting out the window. It prints `rust-lsp: the daemon at <url> reports a doc
index error: <doc_index_error>; run rust-lsp refresh to rebuild it.` on stderr
and exits `2` without running the command. `rust-lsp --wait SECS refresh` is
the exception, because `refresh` is the recovery: it prints `rust-lsp: the
daemon at <url> reports a doc index error: <doc_index_error>; running refresh
to rebuild it.` on stderr and runs the refresh instead of stopping. A
`"ready"` doc index with
`doc_index_error` set (a failed rebuild while the old index keeps answering)
does not stop the wait. A navigation command under `--wait` does not stop on
a doc index error at all: it runs once the code index is ready.

The distinction that matters is what happens when the window *does* expire:

- **Never connected within `SECS`** (exit `3`) — the daemon likely isn't
  running yet, or the CLI is pointed at the wrong URL/port. Start the daemon
  (`docker compose up -d rust-lsp-mcp` — always name the service) or check
  `RLM_HTTP_PORT`/`RLM_CLI_URL`.
- **Connected but never reported `ready`** (exit `2`) — the daemon is up but
  one of the two indexes is still building, or the code index has permanently
  failed (check `state` in a plain `rust-lsp status` call). A larger `--wait`
  won't help a permanently failed index; call `refresh` instead.

`--wait` accepts any finite number of seconds; `nan`/`inf` are rejected at
the argument-parsing stage (they would make the deadline arithmetic never
terminate — a hang, not a timeout).

**Health check:** `rust-lsp --wait <SECS> status` (with a generous `SECS`,
e.g. `180` for the very first call after starting the daemon — e.g.
`docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp --wait 180 status`) is the
recommended way to confirm the daemon is up and the index is ready before
issuing real navigation calls. A plain `rust-lsp status` (no `--wait`) is a
one-shot check with no polling — use it once you already expect the daemon to
be ready and just want the current state.

## `mcp-bridge`: stdio relay for `rlm-connect`

`rust-lsp mcp-bridge` is not a tool call and prints no envelope. It lets an IDE
that speaks MCP only over stdio use the daemon: it reads newline-delimited
JSON-RPC messages from stdin, POSTs each one to
`http://127.0.0.1:<port>/mcp`, and writes each reply message to stdout as one
JSON line. A `text/event-stream` reply is split into its `data:` events, one
line each; a notification answered with `202 Accepted` writes nothing. stdout
carries MCP messages only; the bridge's log goes to stderr.

You normally never run it yourself. The host-side launcher
`scripts/rlm-connect` starts or reuses the project's daemon container and then
execs `docker exec -i rlm-<ID> /app/.venv/bin/rust-lsp mcp-bridge`; see the
[daemon runbook](daemon.md) for the launcher's options (`--up`,
`--print-names`, `--engine`, `--image`, `--network`) and the IDE
configuration.

| Option | Default | What it does |
|---|---|---|
| `--port N` | `$RLM_HTTP_PORT`, else `8000` | The daemon listener's port on `127.0.0.1`. |
| `--listener-timeout SECS` | `120` | Before relaying the first message, try to connect every 0.5 seconds until the listener accepts or `SECS` pass. Messages that arrive meanwhile are held in order, so an IDE's `initialize` sent during a cold start is relayed once the daemon listens. |
| `--log-level LEVEL` | `INFO` | Level of the stderr log: `DEBUG`, `INFO`, `WARNING` or `ERROR`. |

If the listener wait expires, or a connection fails after the listener was
reached, the bridge answers the pending request with a JSON-RPC error (code
`-32000`) on stdout, prints a hint on stderr, and carries on with the next
message. It exits `0` when stdin closes. It imports nothing from the server
package and opens no database, so it never acts as a second writer on the
project volume.

## Daemon logs

View daemon logs via `docker logs rust-lsp-mcp` (or `podman logs`).

During normal operation, logs may display a per-request `ClosedResourceError` traceback. This is benign log noise caused by how the MCP Python SDK tears down stateless HTTP transports. It does not affect command execution or response validity.

## `refresh`: shared-daemon blast radius

`refresh` restarts the single analyzer process and rebuilds the documentation index in the background, inside the container. It returns as soon as both have started. Because all CLI calls and MCP clients share this instance, running `refresh` makes all concurrent navigation queries return `not_ready` until the analyzer finishes indexing. Over a ready doc index, `search-docs` keeps answering from the old index while the doc rebuild runs; over an errored or absent one it returns `not_ready` until the re-initialised index is ready.

Check `rust-lsp status` before refreshing. Only trigger `refresh` when code changes warrant an index rebuild or when `state` reports `"error"`.

## Troubleshooting

| Symptom | Cause | Resolution |
|---|---|---|
| Exit `3`, "daemon not reachable" | The container is stopped or ports are misconfigured. | Run `RUST_PROJECT=/abs/path docker compose up -d rust-lsp-mcp`. Verify `RLM_HTTP_PORT`. |
| Exit `4`, "was reached, but the request timed out" | The daemon is up, but rust-analyzer gave no answer within 300 s; it may be stalled. | Run `rust-lsp status`; if `analyzer_stall` is set, run `rust-lsp refresh`, then `rust-lsp --wait 180 status` before the next call. |
| Exit `2` (`not_ready`) | Analyzer or documentation store is still indexing. | Check `rust-lsp status` or use `--wait 180` to poll until ready. |
| Exit `2` with `state: "error"` | Background indexing failed permanently. | Run `docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp refresh`. |
| Exit `1` (`error`) | Malformed arguments or internal tool failure. | Check `message` and `recovery` in JSON output. Correct inputs if `recovery: "fix_input"`. |
| `docker exec: no such container` | Container was not started or has a different name. | Run `docker ps` to verify container name. |
| Exit `127`: `rust-lsp not found` | Command was invoked without full path. | Invoke `/app/.venv/bin/rust-lsp` directly. |
| `state: "ready"` with `chunk_count: 0` | SELinux denied container read access to the project mount. | Ensure mount includes `:ro,z` suffix, or run `chcon -Rt container_file_t /path/to/project`. |
| Multiple containers conflicting on `/data` | Several server processes share one ChromaDB volume. They take turns on the doc-store lock for start-up and rebuilds. With the same `RLM_PROJECT_ID`, a `refresh` in one rebuilds the index the other serves; different projects keep separate collections. | Give each project its own volume (`rlm-<project-name>-data`). |

## Related pages

- [Tools / API reference](tools.md) — the underlying tool semantics, response
  shapes, and status vocabulary that every `rust-lsp` subcommand shares with
  the MCP tools.
- [Configuration](configuration.md) — `RLM_TRANSPORT`, `RLM_HTTP_PORT`,
  `RLM_IDLE_TIMEOUT_SECONDS`, and the CLI-only `RLM_CLI_URL`.
- [Daemon runbook](daemon.md) — `rlm-connect`: one warm daemon per project,
  shared by every IDE window.
- [Agent skill: `rust-code-navigation`](../../.claude/skills/rust-code-navigation/SKILL.md)
  — helps an agent decide when to reach for these tools at all, MCP or CLI.
