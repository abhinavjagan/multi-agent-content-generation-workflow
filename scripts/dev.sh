#!/usr/bin/env bash
# Run the FastAPI server (uvicorn) and the Vite dev server side-by-side.
#
# Usage:
#   ./scripts/dev.sh           # both api + ui
#   ./scripts/dev.sh api       # only api
#   ./scripts/dev.sh ui        # only ui
#
# Both processes are killed cleanly on Ctrl-C via a shared trap. We bind the
# API to 127.0.0.1 only -- this server has no auth and must never be exposed
# to the public internet (per the comment in src/x_agent/server.py).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_HOST="${X_AGENT_API_HOST:-127.0.0.1}"
API_PORT="${X_AGENT_API_PORT:-8000}"
UI_PORT="${X_AGENT_UI_PORT:-5173}"

mode="${1:-all}"

start_api() {
  echo "==> API on http://${API_HOST}:${API_PORT}"
  if [ -d ".venv" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  exec uvicorn x_agent.server:app \
    --reload \
    --host "$API_HOST" \
    --port "$API_PORT"
}

start_ui() {
  if [ ! -d "frontend/node_modules" ]; then
    echo "==> Installing frontend deps (one-time)"
    (cd frontend && npm install)
  fi
  echo "==> UI on http://127.0.0.1:${UI_PORT}"
  cd frontend
  X_AGENT_API_URL="http://${API_HOST}:${API_PORT}" exec npm run dev
}

case "$mode" in
  api) start_api ;;
  ui)  start_ui ;;
  all)
    pids=()
    cleanup() {
      trap - INT TERM EXIT
      for pid in "${pids[@]:-}"; do
        if kill -0 "$pid" 2>/dev/null; then
          kill "$pid" 2>/dev/null || true
        fi
      done
      wait 2>/dev/null || true
    }
    trap cleanup INT TERM EXIT

    ( start_api ) &
    pids+=("$!")
    ( start_ui )  &
    pids+=("$!")
    wait -n "${pids[@]}"
    cleanup
    ;;
  *)
    echo "unknown mode: $mode (expected: api | ui | all)"
    exit 2
    ;;
esac
