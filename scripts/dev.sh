#!/usr/bin/env bash
# Run entroclaw from this source checkout (the launcher starts the Python agent itself via `uv run`).
#   scripts/dev.sh [workspace-dir] [-c | --session ID]     (workspace defaults to the current directory)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bun run "$ROOT/cli/src/main.ts" "$@"
