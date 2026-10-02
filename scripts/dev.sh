#!/usr/bin/env bash
# Start the agent API in the background and open the terminal UI against it.
#   scripts/dev.sh [workspace-dir]     (defaults to the current directory)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="$(cd "${1:-$PWD}" && pwd)"
PORT="${PORT:-8765}"
LOG="${XDG_STATE_HOME:-$HOME/.local/state}/coding-agent/server.log"
mkdir -p "$(dirname "$LOG")"

(cd "$ROOT/agent" && WORKSPACE="$WORKSPACE" PORT="$PORT" exec uv run --quiet coding-agent serve) >>"$LOG" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT

for _ in $(seq 1 50); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break
  if ! kill -0 "$SERVER" 2>/dev/null; then echo "Agent failed to start; see $LOG" >&2; tail -n 20 "$LOG" >&2; exit 1; fi
  sleep 0.2
done

cd "$ROOT/cli" && bun run src/main.ts --url "http://127.0.0.1:$PORT" "${@:2}"
