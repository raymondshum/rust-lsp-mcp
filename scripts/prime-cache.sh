#!/usr/bin/env bash
# prime-cache.sh — one-time warm of the /data cargo cache for offline analysis.
#
# Downloads the target project's crates.io dependency SOURCES into the shared
# cargo cache volume so the server can later run under `docker run --network none`
# with full (non-degraded) rust-analyzer analysis.
#
# Why `cargo fetch` (not `cargo check`): fetch only DOWNLOADS dependency sources
# — it runs no build scripts and expands no proc-macros. So this network-on step
# executes NO untrusted project code. The code execution (build.rs / proc-macros)
# happens later, under `--network none`, where it cannot exfiltrate anything.
#
# Usage:
#   scripts/prime-cache.sh /abs/path/to/rust/project [volume-or-data-path] [image]
#
#   $1  host path to the Rust project (required)
#   $2  named volume or host dir for /data caches (default: rust-lsp-mcp-data)
#   $3  image name (default: rust-lsp-mcp)
#
# Container engine is auto-detected (docker if its daemon is reachable, else
# podman). Override with CONTAINER_ENGINE=docker|podman.
#
# NOTE ON :ro — the project is mounted READ-WRITE here on purpose: if the project
# has no committed Cargo.lock, `cargo fetch` generates one in the project dir,
# which a read-only mount would block. This is a one-time, operator-controlled
# step and runs no project code. The steady-state server mount stays `:ro`.

set -euo pipefail

PROJECT="${1:?usage: prime-cache.sh /abs/path/to/rust/project [data-volume] [image]}"
DATA="${2:-rust-lsp-mcp-data}"
IMAGE="${3:-rust-lsp-mcp}"

# Is the docker daemon reachable? Guard with `timeout` (when available) so a
# wedged-but-listening daemon can't hang the whole script on `docker info`.
_docker_daemon_up() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 5 docker info >/dev/null 2>&1
  else
    docker info >/dev/null 2>&1
  fi
}

# Pick a container engine: explicit override, else docker (if its daemon
# answers), else podman, else docker as a last resort (so it errors with the
# engine's own message rather than a silent one).
ENGINE="${CONTAINER_ENGINE:-}"
if [ -z "$ENGINE" ]; then
  if command -v docker >/dev/null 2>&1 && _docker_daemon_up; then
    ENGINE=docker
  elif command -v podman >/dev/null 2>&1; then
    ENGINE=podman
  elif command -v docker >/dev/null 2>&1; then
    ENGINE=docker
  else
    echo "prime-cache: no container engine found — install docker or podman" >&2
    exit 1
  fi
fi
if ! command -v "$ENGINE" >/dev/null 2>&1; then
  echo "prime-cache: CONTAINER_ENGINE='$ENGINE' not found on PATH" >&2
  exit 1
fi

if [ ! -d "$PROJECT" ]; then
  echo "prime-cache: project path does not exist: $PROJECT" >&2
  exit 1
fi
if [ ! -f "$PROJECT/Cargo.toml" ]; then
  echo "prime-cache: no Cargo.toml at $PROJECT — is this a Rust project?" >&2
  exit 1
fi

# Resolve to an absolute path so the engine accepts the bind mount.
PROJECT="$(cd "$PROJECT" && pwd)"

# On an SELinux-enforcing host a bind mount needs a relabel suffix so the
# container can read/write it — this applies to BOTH podman and docker on
# RHEL/Fedora, so gate on SELinux, not on the engine. Use the SHARED label ':z':
# the private ':Z' would relabel your source tree with a per-container category
# and break other tools/containers that also mount that directory.
MOUNT_OPTS=""
if command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled 2>/dev/null; then
  MOUNT_OPTS=":z"
fi

echo "prime-cache: [$ENGINE] fetching dependency sources for $PROJECT into '$DATA' ..."
"$ENGINE" run --rm \
  -v "${PROJECT}:/project${MOUNT_OPTS}" \
  -v "${DATA}:/data" \
  --entrypoint cargo \
  "$IMAGE" fetch --manifest-path /project/Cargo.toml

echo "prime-cache: done. You can now run the server with '--network none'."
