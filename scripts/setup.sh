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

# 4. Disable git commit signing inside the container (idempotent).
#    VS Code copies the host ~/.gitconfig into the container on create, which can
#    carry a host-only signing key path (e.g. gpg.format=ssh + an SSH key under
#    /Users/...). That key isn't present in the container, so every commit fails.
#    Signing belongs to the host workflow; force it off for the container copy.
echo "setup: disabling git commit signing for the container ..." >&2
git config --global commit.gpgsign false

echo "setup: done." >&2
