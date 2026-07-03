# podman — loopback under `--network none`; exec channel fidelity

**Tool:** podman 5.8.2 · **Verified:** 2026-07-02 (live, in the dev container)
· For plan [cli-frontend.md](../planning/cli-frontend.md) (U3/U7).

## Loopback TCP under `--network none` — works

`podman run --network none` still provisions the `lo` interface inside the
container's network namespace (only external interfaces are disabled). Proven
live: `python3 -m http.server 8000 --bind 127.0.0.1` in the container, client
in the same container got HTTP 200. Note `/sys/class/net/lo/operstate` reads
`unknown` — normal for loopback, not a problem; the socket round-trip is the
authoritative check. Consequence: the daemon's loopback listener works
unchanged in the compose `-isolated` (`network_mode: none`) variant.

## `podman exec` (non-TTY) — full channel fidelity

For the skill pattern `{docker|podman} exec <container> rust-lsp <cmd>`
(parse JSON from stdout, read exit code):

- stdout/stderr stay **separated** through exec (`1>out 2>err` captured
  exactly `OUT` / `ERR`).
- Exit code propagates exactly (in-container `exit 7` → host `$?` = 7).
- No `-t` needed; without a TTY output is byte-clean (`od -c` shows pure
  `\n`, no `\r` injection).
- `podman exec -i <c> cat` forwards piped stdin correctly.

No podman-specific adjustment needed vs docker exec for the CLI/skill design.
