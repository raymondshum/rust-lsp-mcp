#!/usr/bin/env bash
# clone-ripgrep.sh — idempotent pinned clone of the ripgrep fixture.
#
# Clones a fixed ripgrep release tag into the bind-mounted source directory.
# Safe to re-run: exits 0 immediately if the directory already exists.
#
# Usage (inside the container after bind mounts are active):
#   bash scripts/clone-ripgrep.sh
#
# The target directory is read from the environment (RLM_PROJECT_ROOT, or the
# deprecated RLM_RIPGREP_SRC alias) or falls back to the devcontainer default path.

set -euo pipefail

# Pinned ripgrep release — update this tag to upgrade the fixture.
RIPGREP_TAG="14.1.1"
RIPGREP_REPO="https://github.com/BurntSushi/ripgrep.git"

# Prefer the current name; fall back to the deprecated alias, then the default.
TARGET="${RLM_PROJECT_ROOT:-${RLM_RIPGREP_SRC:-/workspaces/ripgrep}}"

if [[ -d "$TARGET/.git" ]]; then
    echo "clone-ripgrep.sh: fixture already present at $TARGET — skipping clone." >&2
    exit 0
fi

echo "clone-ripgrep.sh: cloning ripgrep $RIPGREP_TAG into $TARGET ..." >&2
mkdir -p "$TARGET"
git clone \
    --depth 1 \
    --branch "$RIPGREP_TAG" \
    "$RIPGREP_REPO" \
    "$TARGET"
echo "clone-ripgrep.sh: done." >&2
