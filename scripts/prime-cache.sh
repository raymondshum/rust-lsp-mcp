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
# NOTE ON :ro — the project is mounted READ-WRITE here on purpose: if the project
# has no committed Cargo.lock, `cargo fetch` generates one in the project dir,
# which a read-only mount would block. This is a one-time, operator-controlled
# step and runs no project code. The steady-state server mount stays `:ro`.

set -euo pipefail

PROJECT="${1:?usage: prime-cache.sh /abs/path/to/rust/project [data-volume] [image]}"
DATA="${2:-rust-lsp-mcp-data}"
IMAGE="${3:-rust-lsp-mcp}"

if [ ! -d "$PROJECT" ]; then
  echo "prime-cache: project path does not exist: $PROJECT" >&2
  exit 1
fi
if [ ! -f "$PROJECT/Cargo.toml" ]; then
  echo "prime-cache: no Cargo.toml at $PROJECT — is this a Rust project?" >&2
  exit 1
fi

# Resolve to an absolute path so Docker accepts the bind mount.
PROJECT="$(cd "$PROJECT" && pwd)"

echo "prime-cache: fetching dependency sources for $PROJECT into '$DATA' ..."
docker run --rm \
  -v "${PROJECT}:/project" \
  -v "${DATA}:/data" \
  --entrypoint cargo \
  "$IMAGE" fetch --manifest-path /project/Cargo.toml

echo "prime-cache: done. You can now run the server with '--network none'."
