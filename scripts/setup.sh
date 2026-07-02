#!/usr/bin/env bash
# setup.sh — idempotent dev-environment bootstrap. Normally runs inside the
# container (devcontainer.json postCreateCommand), but teardown.sh's own
# final line tells the user to re-run this script afterward, which may
# happen on the HOST. The container-only steps below (git signing) detect
# and skip themselves when not in a container.
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

# Detects Docker and Podman devcontainers, plus common CI/editor container
# signals. Used to gate steps that must NEVER run on the host (see below).
_in_container() {
  [ -f /.dockerenv ] || [ -f /run/.containerenv ] || [ -n "${REMOTE_CONTAINERS:-}" ] || [ -n "${CODESPACES:-}" ] || [ -n "${DEVCONTAINER:-}" ]
}

echo "setup: starting ..." >&2

# 1. Clone ripgrep fixture (idempotent — skips if already present).
bash "${REPO_ROOT}/scripts/clone-ripgrep.sh"

# 2. Generate .env (idempotent — skips if already present; use --force to replace).
bash "${REPO_ROOT}/scripts/init.sh"

# 3. Sync Python dependencies.
echo "setup: running uv sync ..." >&2
uv sync --directory "${REPO_ROOT}"

# 4. Disable git commit signing, but ONLY inside a container (idempotent).
#    VS Code copies the host ~/.gitconfig into the container on create, which can
#    carry a host-only signing key path (e.g. gpg.format=ssh + an SSH key under
#    /Users/...). That key isn't present in the container, so every commit fails.
#    Signing belongs to the host workflow; force it off for the container copy.
#    This must NEVER run on the host — `git config --global` would silently
#    disable commit signing for every repo the developer has, not just this one.
if _in_container; then
    echo "setup: disabling git commit signing for the container ..." >&2
    git config --global commit.gpgsign false
else
    echo "setup: not in a container — leaving global git commit.gpgsign untouched (host signing preserved)." >&2
fi

echo "setup: done." >&2
