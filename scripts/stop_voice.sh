#!/usr/bin/env bash
# Stop the host-side x-agent voice sidecar started by start_voice.sh.
# Idempotent: a no-op if no sidecar is running.

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${ROOT_DIR}/.runtime/voice_sidecar.pid"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
ok()   { printf '%s\n' "${GREEN}✓${RESET} $*"; }
warn() { printf '%s\n' "${YELLOW}!${RESET} $*"; }
dim()  { printf '%s\n' "${DIM}$*${RESET}"; }

if [ ! -f "$PID_FILE" ]; then
  dim "no voice sidecar pid file at ${PID_FILE}"
  exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [ -z "$PID" ]; then
  warn "empty pid file -- removing"
  rm -f "$PID_FILE"
  exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
  dim "voice sidecar PID $PID not running"
  rm -f "$PID_FILE"
  exit 0
fi

kill "$PID" 2>/dev/null || true
# Give uvicorn a moment to wind down.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ! kill -0 "$PID" 2>/dev/null; then break; fi
  sleep 0.3
done
if kill -0 "$PID" 2>/dev/null; then
  warn "PID $PID didn't exit gracefully -- sending SIGKILL"
  kill -9 "$PID" 2>/dev/null || true
fi
rm -f "$PID_FILE"
ok "voice sidecar stopped (PID $PID)"
