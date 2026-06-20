#!/usr/bin/env bash
# teardown.sh — SOLE destructive reset for the dev environment.
#
# Removes:
#   - ripgrep source fixture (.devcontainer/cache/ripgrep-src)
#   - cargo build output (.devcontainer/cache/cargo-target)
#   - CARGO_HOME cache (.devcontainer/cache/cargo-home)
#   - ChromaDB vector store (.devcontainer/cache/chroma)
#   - ONNX model cache (.devcontainer/cache/chroma-model-cache)
#   - .env
#   - Python virtual environment (.venv)
#   - Python build artifacts (dist/, __pycache__, *.egg-info)
#
# DISTINCT from the product's "refresh" operation:
#   - teardown wipes everything including the analyzer cache (CARGO_HOME, target/)
#   - refresh (Phase 4) does a wholesale re-index but must NEVER wipe CARGO_HOME
#     or the build artifacts that survive between sessions.
#
# Usage (inside the project root, inside or outside the container):
#   bash scripts/teardown.sh
#
# After teardown, run `bash scripts/setup.sh` to restore the environment.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${REPO_ROOT}/.devcontainer/cache"

warn() { echo "teardown: $*" >&2; }
rm_if() {
    local target="$1"
    if [[ -e "$target" ]]; then
        warn "removing $target"
        rm -rf "$target"
    else
        warn "(not found, skipping) $target"
    fi
}

warn "=== DESTRUCTIVE RESET — this cannot be undone ==="
warn "Sleeping 3 seconds — Ctrl-C to abort ..."
sleep 3

# Bind-mount caches
rm_if "${CACHE_DIR}/ripgrep-src"
rm_if "${CACHE_DIR}/cargo-target"
rm_if "${CACHE_DIR}/cargo-home"
rm_if "${CACHE_DIR}/chroma"
rm_if "${CACHE_DIR}/chroma-model-cache"

# Local config
rm_if "${REPO_ROOT}/.env"

# Python environment and build artifacts
rm_if "${REPO_ROOT}/.venv"
rm_if "${REPO_ROOT}/dist"

# __pycache__ and egg-info (non-critical, but keep the tree clean)
find "${REPO_ROOT}/src" "${REPO_ROOT}/tests" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${REPO_ROOT}/src" -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

warn "teardown complete. Run 'bash scripts/setup.sh' to restore."
