#!/usr/bin/env bash
# setup.sh — idempotent dev-environment bootstrap (runs inside the container).
#
# Steps (all idempotent):
#   1. Clone the pinned ripgrep fixture if not already present.
#   2. Generate .env from env.sample if .env is absent.
#   3. Sync Python dependencies with uv.
#
# Called by devcontainer.json postCreateCommand and safe to re-run at any time.
# This script must NEVER be destructive — teardown.sh handles resets.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "setup: starting ..." >&2

# 1. Clone ripgrep fixture (idempotent — skips if already present).
bash "${REPO_ROOT}/scripts/clone-ripgrep.sh"

# 2. Generate .env (idempotent — skips if already present; use --force to replace).
bash "${REPO_ROOT}/scripts/init.sh"

# 3. Sync Python dependencies.
echo "setup: running uv sync ..." >&2
uv sync --directory "${REPO_ROOT}"

echo "setup: done." >&2
