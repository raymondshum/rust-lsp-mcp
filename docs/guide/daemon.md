---
okf_version: "0.1"
type: Guide
title: Daemon runbook (rlm-connect)
description: Warm, check and attach to one rust-lsp-mcp daemon per project with rlm-connect -- names, cache priming, readiness, logs, idle timeout, IDE configuration and recovery.
tags: [guide, tier-a, daemon, operations]
timestamp: 2026-11-04T00:00:00Z
source_pins:
  - path: scripts/rlm-connect
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_cli/bridge.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: src/rust_lsp_mcp/server.py
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
  - path: docker-compose.yml
    commit: 72b605385dc5f3568be456b5fd18e685ceb1d20b
cite:
  - scripts/rlm-connect:BRIDGE_ARGV
  - src/rust_lsp_cli/bridge.py:DEFAULT_LISTENER_TIMEOUT_SECONDS
  - src/rust_lsp_mcp/server.py:IdleTracker
---

[← Back to the README](../../README.md) · [Documentation index](index.md)

# Daemon runbook (`rlm-connect`)

`rlm-connect` runs one long-lived `rust-lsp-mcp` daemon per project folder and
connects every IDE window on that folder to it over stdio. rust-analyzer's cold
start and the documentation embed happen once per daemon, not once per window,
and the daemon is the only process that writes the project's volume.

How it fits together:

- **`rlm-connect`** (`scripts/rlm-connect`, Python 3 standard library only) runs
  on the host. It hashes the folder's real path to a 12-character ID and derives
  the container name `rlm-<ID>`, the volume name `rlm-<ID>-data` and
  `RLM_PROJECT_ID=<ID>`. If the container is absent it creates it; if it is
  stopped it starts it; if it is running it leaves it alone. Then it replaces
  itself with `docker exec -i rlm-<ID> /app/.venv/bin/rust-lsp mcp-bridge`.
- **`rust-lsp mcp-bridge`** runs inside the container and relays MCP messages
  between its stdio and the daemon's listener on `127.0.0.1:8000`. It opens no
  database and loads no index. On a cold start it waits up to 120 seconds for
  the listener and holds the IDE's first messages until it accepts
  ([CLI reference](cli.md#mcp-bridge-stdio-relay-for-rlm-connect)).
- **The daemon** is the image's `rust-lsp-mcp` in streamable-HTTP mode, with
  `--network none`, the project mounted read-only, and an idle timeout. No port
  is published on the host.

## Before you start

1. Build the image once from this repository's root: `docker build -t rust-lsp-mcp .`
   (or `podman build -t rust-lsp-mcp .`). `rlm-connect` and
   `scripts/prime-cache.sh` both default to the image name `rust-lsp-mcp`.
   After you rebuild the image, an existing `rlm-<ID>` container keeps running
   the old image until you run `docker rm -f rlm-<ID>` (the volume stays).
2. Put `rlm-connect` on your `PATH`, for example
   `ln -s "$PWD/scripts/rlm-connect" ~/.local/bin/rlm-connect`, or call it by
   its absolute path everywhere below.

`rlm-connect` uses `docker` when it is on `PATH`, else `podman`; pass
`--engine podman` to choose podman explicitly. With podman, read `podman` for
`docker` in every command on this page.

## Warm and check a daemon from the shell

Do these steps before opening the IDE, so a slow start or a failed load shows
up in your shell instead of as a silent IDE hang.

1. **Print the names.** Run `rlm-connect --print-names /abs/path/to/project`.
   You see two lines, the container name and then the volume name:

   ```
   rlm-528c33294931
   rlm-528c33294931-data
   ```

   This calls no container engine.

2. **Prime the cargo cache.** Run
   `scripts/prime-cache.sh /abs/path/to/project rlm-528c33294931-data`, with the
   volume name from the second line of step 1. It downloads the dependency
   sources and ends with `prime-cache: done.`. The daemon runs with
   `--network none`, so on a fresh per-project volume cargo cannot fetch
   anything, and the analyzer comes up with a non-ok `analyzer_health` while
   `state` reads `ready`. Re-run this step after a pull or a branch switch that
   changes `Cargo.lock`.

3. **Start the daemon.** Run `rlm-connect --up /abs/path/to/project`. It prints
   the container name, for example `rlm-528c33294931`, and exits without
   attaching. Its diagnostics (absent, stopped or running, and what it did) go
   to stderr.

4. **Wait for ready.** Run
   `docker exec rlm-528c33294931 /app/.venv/bin/rust-lsp --wait 600 status`. It
   polls until both the code index and the doc index are ready, then prints the
   envelope and exits `0`. Check these fields:

   ```json
   {
     "status": "ok",
     "state": "ready",
     "analyzer_health": "ok",
     "analyzer_health_message": null,
     "indexed_commit": "4649aa9700619f94cf9c66876e9549d83420e16c",
     "current_commit": "4649aa9700619f94cf9c66876e9549d83420e16c",
     "stale": false,
     "doc_index_state": "ready",
     "doc_index_chunk_count": 500,
     "doc_stale": false
   }
   ```

   - `analyzer_health` must be `"ok"`. A `"warning"` or `"error"` with
     `state: "ready"` usually means the cache was not primed (step 2); the
     reason is in `analyzer_health_message`.
   - `stale: true` means the project's checked-out commit moved since the code
     index was built; `doc_stale: true` means the same for the doc index. Run
     step 5.

   On the ripgrep sample, the first start on a fresh, primed volume reached
   this envelope in about a minute, most of it spent embedding the docs. A
   restart that reuses the volume took about 5 seconds. A first start on an
   unprimed volume under `--network none` (the `rlm-connect` default) takes
   about 10 minutes to reach `state: "ready"` and ends with a non-ok
   `analyzer_health`, so `--wait 600` can expire first; its exit `2` then
   means the daemon is still indexing. Stopping the daemon (`docker rm -f rlm-<ID>`), priming
   the volume with step 2 and starting it again with step 3 is faster.

5. **Refresh after a branch switch.** Run
   `docker exec rlm-528c33294931 /app/.venv/bin/rust-lsp refresh`. It returns at
   once with `state: "indexing"`; repeat step 4 to wait for `ready`. `refresh`
   affects every window attached to this daemon
   ([KI-14](../impl/known-issues.md)).

6. **Follow the logs.** Run `docker logs -f rlm-528c33294931`. The phase lines
   to expect, in order:

   ```
   ... INFO rust_lsp_mcp.server: idle timeout: 3600 seconds
   ... INFO rust_lsp_mcp.server: starting streamable-http daemon on 127.0.0.1:8000
   ... INFO rust_lsp_mcp.analyzer: AnalyzerManager: analyzer start (gen 0)
   ... INFO rust_lsp_mcp.analyzer: rust-analyzer progress: Roots Scanned 62/62
   ... INFO rust_lsp_mcp.analyzer: rust-analyzer progress: Indexing
   ... INFO rust_lsp_mcp.analyzer: AnalyzerManager: rust-analyzer ready (state=ready)
   ... INFO rust_lsp_mcp.doc_store: doc_store: rebuild complete, 0 files unchanged, 18 re-embedded, 500 chunks, 52.8 seconds
   ```

   The `doc_store: rebuild complete` line appears only when the doc index was
   rebuilt: on a first start, after `refresh`, or when the project's docs
   changed. When the daemon adopts the index already on the volume, it logs
   `doc_store: adopting existing collection (... chunks) — skipping rebuild`
   instead. A `ClosedResourceError` traceback per request is benign
   ([KI-15](../impl/known-issues.md)).

7. **Open the IDE.** The IDE runs `rlm-connect` (see
   [IDE configuration](#ide-configuration)), finds the container running, leaves
   it alone and attaches a bridge, so the window uses the warm daemon at once.

## How ingest and refresh run

Indexing and `refresh` always run inside the daemon, triggered through the
`rust-lsp` CLI (steps 4 and 5) or an MCP tool call. No offline ingest command
exists, so under `rlm-connect` the daemon is the only process that writes the
project's volume. A second server started by hand on the same volume with the
same `RLM_PROJECT_ID` (`rlm-connect` sets `RLM_PROJECT_ID=<ID>`) takes turns
with the daemon on the doc-store lock, which covers the store's construction,
the adopt check and every rebuild. Its doc search picks up the daemon's
rebuilt index on its next call ([KI-13](../impl/known-issues.md), resolved).
With another `RLM_PROJECT_ID`, or none, it builds and serves a separate
collection on the volume.

## How a daemon ends

- **Idle timeout.** The daemon exits with code `0` when no request has arrived
  for `RLM_IDLE_TIMEOUT_SECONDS` (default `3600`; `0` disables it). It logs
  `idle timeout reached after <n> seconds; exiting`. A request in progress
  holds the timeout off. Only HTTP requests count as activity: indexing does
  not, so set the value above the daemon's cold start, or the daemon exits
  before `ready` on every start. On the ripgrep sample the cold start takes
  about a minute on a primed volume, and a first start on an unprimed
  volume under `--network none` (the `rlm-connect` default) takes about 10
  minutes to reach `state: "ready"` and ends with a non-ok `analyzer_health`.
  The next `rlm-connect` starts the stopped container again.
- **`docker stop rlm-<ID>`** on a daemon that has reached `ready` stops it in
  about a second (0.8 to 0.9 seconds measured with podman on the ripgrep
  sample, with and without an attached `rlm-connect` window). The container
  then reports exit code `143` (terminated by SIGTERM), not `0`, and an
  attached window's bridge ends. A stop during the cold index or a re-index
  takes several seconds, because the daemon first cancels the doc index build
  and then tears down the analyzer: about 3 to 3.5 seconds during a re-index
  after `refresh`, and about 5 to 5.6 seconds 7 to 10 seconds into a cold
  start. In the first seconds of a cold start a stop can take longer than
  docker's 10-second default timeout. The engine then kills the container
  (podman logs `resorting to SIGKILL`) and it reports exit code `137`, as
  measured 2 seconds into a cold start (10.3 seconds). `docker stop -t 15
  rlm-<ID>` avoids that: the same stop finished in 10.25 seconds with exit
  code `143`. `docker rm rlm-<ID>` also removes the container; the volume,
  with its cargo cache and doc index, stays.

`RLM_IDLE_TIMEOUT_SECONDS` set in the environment that runs `rlm-connect`
reaches the daemon only when `rlm-connect` creates the container. Starting a
stopped container reuses the environment it was created with, so to change the
value, run `docker rm -f rlm-<ID>` and start it again with the new value set:

```bash
docker rm -f rlm-528c33294931
RLM_IDLE_TIMEOUT_SECONDS=7200 rlm-connect --up /abs/path/to/project
```

The compose daemon (`docker compose up -d rust-lsp-mcp`) uses
`restart: on-failure`: a crash with a non-zero exit code restarts it, but after
an idle exit (code `0`) it stays stopped until you run `docker compose up -d`
again.

## Recover an IDE window after the daemon stops

An idle exit or `docker stop` stops the container, which ends every attached
`docker exec` bridge, so each IDE window reports the MCP server as stopped.
Restarting the server in the IDE re-runs `rlm-connect`, which starts the
stopped container again and attaches a new bridge.

- **VS Code:** run the **MCP: List Servers** command, select the server, and
  choose restart. (The VS Code reference describes this command as "List all
  configured MCP servers and perform actions like start, stop, restart, or show
  output.")
- **Claude Desktop:** quit the app and open it again.

## IDE configuration

**VS Code, workspace** (`.vscode/mcp.json` in the project):

```json
{
  "servers": {
    "rust-lsp-mcp": {
      "type": "stdio",
      "command": "rlm-connect",
      "args": ["${workspaceFolder}"]
    }
  }
}
```

The VS Code MCP configuration reference does not say how a server in the
user-profile `mcp.json` resolves its project folder, so this runbook gives only
the workspace form above and the Claude Desktop form below.

**Claude Desktop** (`claude_desktop_config.json`): give the project path
explicitly, and give `rlm-connect` by absolute path, because the app does not
read your shell's `PATH`:

```json
{
  "mcpServers": {
    "rust-lsp-mcp": {
      "command": "/abs/path/to/rust-lsp-mcp/scripts/rlm-connect",
      "args": ["/abs/path/to/project"]
    }
  }
}
```

Add `"--engine", "podman"` before the folder in `args` to use podman when
docker is also installed, and `"--network", "default"` for a project whose
build scripts need a live network.

## Troubleshooting

| Symptom | Cause | Resolution |
|---|---|---|
| `state: "ready"` with `analyzer_health: "warning"` or `"error"` | The volume's cargo cache was not primed, or `Cargo.lock` changed since. | Run step 2 with the volume name from step 1, then `rust-lsp refresh`. |
| The IDE reports the MCP server stopped right after it started, or mid-session | The daemon exited (it crashed, failed at startup, idled out or was stopped). The bridge runs inside the container, so the daemon's exit ends the attached bridge process too: no error reply reaches the IDE and pending requests stay unanswered. | `docker logs rlm-<ID>` for the cause; then restart the server in the IDE ([above](#recover-an-ide-window-after-the-daemon-stops)). |
| An MCP error "rust-lsp-mcp daemon not reachable" | The container is up but its listener is not: the 120-second listener wait expired, or a connection was refused. | `docker logs rlm-<ID>`; then `docker exec rlm-<ID> /app/.venv/bin/rust-lsp --wait 600 status`. |
| `rlm-connect: container engine ... not found` | Neither docker nor podman is on `PATH`. | Install one, or pass `--engine`. |
| A new `RLM_IDLE_TIMEOUT_SECONDS` has no effect | The container already existed. | `docker rm -f rlm-<ID>`, then `rlm-connect --up` with the value set. |

## Related pages

- [CLI reference](cli.md) — `rust-lsp` subcommands, `--wait`, and `mcp-bridge`.
- [Configuration](configuration.md) — `RLM_IDLE_TIMEOUT_SECONDS` and the other settings.
- [Known issues](../impl/known-issues.md) — KI-13 (resolved: processes sharing a volume take turns on the doc-store lock) and KI-14 (`refresh` blast radius).
