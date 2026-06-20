#!/usr/bin/env bash
# init.sh — generate .env from env.sample.
#
# Usage:
#   bash scripts/init.sh           # safe: skips if .env already exists
#   bash scripts/init.sh --force   # overwrites existing .env
#
# The server loads .env itself via pydantic-settings; run this once after
# cloning to get a local .env that you can customise.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SAMPLE="${REPO_ROOT}/env.sample"
DOTENV="${REPO_ROOT}/.env"

FORCE=false
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

if [[ -f "$DOTENV" && "$FORCE" == false ]]; then
    echo "init.sh: .env already exists (use --force to overwrite). Skipping." >&2
    exit 0
fi

cp "$SAMPLE" "$DOTENV"
echo "init.sh: wrote $DOTENV from $SAMPLE"
