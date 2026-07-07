---
okf_version: "0.1"
type: Guide
title: CLI reference (rust-lsp)
description: Command-line client reference for rust-lsp -- subcommands, exit codes, and --wait semantics for agents without MCP tool access.
tags: [guide, tier-a, cli]
timestamp: 2026-07-07T00:00:00Z
source_pins:
  - path: src/rust_lsp_cli/cli.py
    commit: b13c90f6e0dd0301198e3f2c324d956a4a14d74a
  - path: src/rust_lsp_cli/client.py
    commit: b13c90f6e0dd0301198e3f2c324d956a4a14d74a
cite:
  - src/rust_lsp_cli/cli.py:EXIT_NOT_READY
---

[← Back to the README](../../README.md) · [Documentation index](index.md)

# CLI reference (`rust-lsp`)

This page documents `rust-lsp`, a command-line client for the same read-only
tools the MCP server exposes. It exists
for agents that can run shell commands but can't call MCP tools directly —
for example a subagent that doesn't inherit its parent's MCP tool access. If
your assistant already has MCP tool access, use that instead; `rust-lsp` is a
second entry point to the same functionality, not a replacement for it. See
the README's [CLI access](../../README.md#cli-access-for-agents-without-mcp)
section for how to start the daemon this client talks to.

`rust-lsp` is intentionally thin: it never opens rust-analyzer or the
documentation store itself. It connects over HTTP to a long-lived
**daemon** — a `rust-lsp-mcp` server started with
`RLM_TRANSPORT=streamable-http` (see [Configuration](configuration.md)) —
and every call is one round trip to that already-warm process. All the
actual analysis logic, response shapes, and status vocabulary are shared with
the MCP tools and documented in the [Tools / API reference](tools.md); this
page covers the parts that are specific to invoking them from a shell.

## How it connects

By default, `rust-lsp` talks to `http://127.0.0.1:${RLM_HTTP_PORT:-8000}/mcp`
— the same port the daemon reads from `RLM_HTTP_PORT`, so the two can't
silently disagree. Set `RLM_CLI_URL` to a non-empty value to override the
target URL outright (e.g. to point at a non-default port; an
exported-but-empty value is treated as unset); see
[Configuration](configuration.md#a-cli-only-variable-rlm_cli_url) for why
this variable is CLI-only and not in `env.sample`.

In the shipped Docker setup you don't run `rust-lsp` on your host — you run
it *inside* the daemon's container, where the loopback address it connects to
actually exists, via `docker exec` (or `podman exec` — identical usage):

```
docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp status
```

**Use the full path, `/app/.venv/bin/rust-lsp`.** The production image's
`PATH` only adds `/usr/local/cargo/bin` (for rust-analyzer); it does not add
`/app/.venv/bin`, so a bare `rust-lsp` under `docker exec`/`podman exec` fails
with "command not found." Every example on this page that runs inside that
container uses the full path for this reason.

If you're already in a shell where `rust-lsp` resolves on `PATH` some other
way — for example, `uv run rust-lsp` from this project's own dev container,
or a shell with the daemon's venv activated — you can drop both the `docker
exec rust-lsp-mcp` prefix and the `/app/.venv/bin/` path, and just run
`rust-lsp <cmd>` directly. The [Subcommands](#subcommands) table below uses
the short `rust-lsp <cmd>` form throughout to document the command's own
syntax; substitute whichever prefix your shell actually needs.

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
| `2` | `not_ready` (`EXIT_NOT_READY`) — the daemon answered but the analyzer or doc index is still (re)building. Also used when a `--wait SECS` window expired while the daemon *was* reachable but never reported `ready`. |
| `3` | The daemon could not be reached at all — connection refused, handshake failure, or timeout. Also used when a `--wait SECS` window expired without the daemon ever answering. Stderr prints a hint to start the daemon. |
| `2` (argparse) | A command-line usage error (bad or missing arguments, unknown subcommand) also exits `2`, from argparse's own default — this numerically collides with `not_ready` above but is never ambiguous in practice: a usage error happens *before* any network call and prints its own distinct usage message on stderr, whereas `not_ready` always comes with a printed JSON envelope on stdout. |

## `--wait SECS`: riding out daemon boot

`--wait` is a **global** option and must come *before* the subcommand name:

```
rust-lsp --wait 180 status
```

(not `rust-lsp status --wait 180` — argparse's subparser mechanics require
global options ahead of the subcommand).

With `--wait SECS` given, `rust-lsp` polls the daemon's `status` tool every 2
seconds, for up to `SECS` seconds, before running your actual command. This
is meant to absorb the daemon's first-use indexing window (which can take
minutes on a large project) in one command instead of a hand-rolled retry
loop. It rides through the *entire* boot sequence, not just the "already
connected but indexing" phase: a daemon that isn't listening yet moves
through connection-refused → `not_ready` → `ready`, and `--wait` treats a
connection failure exactly like `not_ready` **as long as the window hasn't
expired** — it keeps polling either way.

The distinction that matters is what happens when the window *does* expire:

- **Never connected within `SECS`** (exit `3`) — the daemon likely isn't
  running yet, or the CLI is pointed at the wrong URL/port. Start the daemon
  (`docker compose up -d rust-lsp-mcp` — always name the service) or check
  `RLM_HTTP_PORT`/`RLM_CLI_URL`.
- **Connected but never reported `ready`** (exit `2`) — the daemon is up but
  stuck indexing (or has permanently failed — check `state` in a plain
  `rust-lsp status` call). A larger `--wait` won't help a permanently failed
  index; call `refresh` instead.

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

## Daemon logs

The daemon's stderr goes wherever the container's stdout/stderr are captured
— `docker logs rust-lsp-mcp` (or `podman logs`). You may see a per-request
`ClosedResourceError` traceback in there during normal operation: this is
**benign log noise**, not a failure. It comes from how the pinned MCP SDK
(1.12.4) tears down each stateless HTTP request's transport internally, and
it does not affect the response the CLI actually receives. See
[known issue KI-15](../impl/known-issues.md#ki-15--benign-closedresourceerror-log-noise-in-stateless-http-mode)
for the tracking detail; it's expected to be revisited on a future SDK
upgrade, not something to act on today.

## `refresh`: shared-daemon blast radius

`refresh` (see the [Tools / API reference](tools.md#refresh) for the full
tool semantics) tears down and rebuilds the **one** analyzer and doc index
the daemon runs — there is only one daemon per project, and every CLI call
and every other MCP client hitting the same daemon shares it. Calling
`rust-lsp refresh` while other callers are mid-task means every one of their
next navigation calls returns `not_ready` until re-indexing finishes. Check
`rust-lsp status` first; only call `refresh` when the index is actually stale
or `state`/`doc_index_state` reports `"error"`. This caveat is inherent to
the shared-daemon design (documented as
[known issue KI-14](../impl/known-issues.md#ki-14--refresh-blast-radius-on-a-shared-daemon)),
not a bug — there is no per-caller isolation to lose.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Exit `3`, stderr says "daemon not reachable" | The daemon container isn't running, or `RLM_HTTP_PORT`/`RLM_CLI_URL` points at the wrong place. | From this repo's directory: `RUST_PROJECT=/abs/path docker compose up -d rust-lsp-mcp` (or `-isolated`), then retry — ideally with `docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp --wait 180 status` on the first call. |
| Exit `2` (envelope `not_ready`) | The daemon is up but the analyzer or doc index is still building, or `--wait` expired before it finished. | Re-run `docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp status` to see `state`/`doc_index_state`, or retry with a larger `--wait`. |
| Exit `2` (from a `--wait` window) and `status` shows `state: "error"` | The background indexing run failed permanently — waiting longer won't help. | `docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp refresh` (mind the [blast radius](#refresh-shared-daemon-blast-radius) above), then `--wait` again. |
| Exit `1` (envelope `error`) | Bad input (see `message`/`recovery` in the printed JSON) or an unexpected internal/LSP failure. | Fix the argument per `recovery: "fix_input"`, or treat `recovery: "unknown"` as a report-worthy failure. |
| `docker exec: no such container` | The daemon service was never started, or was started under a different container name. | Check `docker ps`; start it with `RUST_PROJECT=/abs/path docker compose up -d rust-lsp-mcp` from this repo's directory (default container name `rust-lsp-mcp`). |
| `docker exec`/`podman exec` fails immediately with an "executable file not found in $PATH" error (exit `127`; exact wording differs by engine — e.g. podman's crun reports `` executable file `rust-lsp` not found in $PATH ``) | Ran a bare `rust-lsp` — the image's `PATH` doesn't include `/app/.venv/bin`. | Use the full path: `docker exec rust-lsp-mcp /app/.venv/bin/rust-lsp <cmd>`. |
| `status` says `state: "ready"` but `doc_index_chunk_count: 0`, and/or every navigation call returns an `error` envelope like "LSP error: File read failed …" (`recovery: "unknown"`) — retrying never helps | The container cannot **read** the mounted project — classically SELinux on an enforcing host (e.g. rootless podman on Fedora): the bind mount succeeds but `container_t` is denied read on the host files, which is masked as a "healthy" empty doc index plus generic per-call read errors (see [known issue KI-16](../impl/known-issues.md#ki-16--unreadable-project-mount-eg-selinux-is-masked-as-ready-with-zero-docs--generic-errors)). | Make sure the project mount carries the shared SELinux label — the shipped `docker-compose.yml` already mounts `/project` with `:ro,z` — or relabel host-side: `chcon -Rt container_file_t /abs/path/to/project`. Do **not** just retry; the error is access, not transience. |
| Two containers seem to be fighting over the doc index | A second server process (not `rust-lsp` itself — the CLI never opens the doc store) is running against the same `/data` volume. | Run exactly one server per Chroma path — see [known issue KI-13](../impl/known-issues.md#ki-13--chromadb-cross-process-single-writer-hazard-on-a-shared-data-volume). |

## Related pages

- [Tools / API reference](tools.md) — the underlying tool semantics, response
  shapes, and status vocabulary that every `rust-lsp` subcommand shares with
  the MCP tools.
- [Configuration](configuration.md) — `RLM_TRANSPORT`, `RLM_HTTP_PORT`, and
  the CLI-only `RLM_CLI_URL`.
- [Agent skill: `rust-code-navigation`](../../.claude/skills/rust-code-navigation/SKILL.md)
  — helps an agent decide when to reach for these tools at all, MCP or CLI.
