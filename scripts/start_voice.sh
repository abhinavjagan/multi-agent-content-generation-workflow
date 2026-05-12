#!/usr/bin/env bash
# Launch the x-agent voice sidecar (Kokoro TTS + faster-whisper STT)
# as a host-side service on http://127.0.0.1:8765 -- exactly the same
# pattern Ollama uses on :11434.
#
# Why a sidecar?
# - Container CA bundles often don't trust corporate TLS-intercepting
#   roots (Zscaler, Cisco Secure Access, etc). The host does. Running
#   model downloads on the host side-steps that problem entirely.
# - The container needs ZERO outbound network for voice; it just talks
#   to host.docker.internal:8765 over plain HTTP on the loopback
#   bridge.
#
# Idempotent: re-running while the sidecar is up just prints status.
# Logs go to .runtime/voice_sidecar.log, PID to .runtime/voice_sidecar.pid.
#
# Usage:
#   ./scripts/start_voice.sh                # start (or report already-running)
#   ./scripts/start_voice.sh --foreground   # run in foreground (Ctrl+C to stop)
#   ./scripts/start_voice.sh --restart      # stop then start
#   ./scripts/start_voice.sh --status       # just print state, don't change it
#
# Stop with: ./scripts/stop_voice.sh  (also called by ./scripts/stop.sh)

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
ok()    { printf '%s\n' "${GREEN}✓${RESET} $*"; }
info()  { printf '%s\n' "${BOLD}→${RESET} $*"; }
warn()  { printf '%s\n' "${YELLOW}!${RESET} $*"; }
fail()  { printf '%s\n' "${RED}✗${RESET} $*" >&2; }
dim()   { printf '%s\n' "${DIM}$*${RESET}"; }

FOREGROUND=0
RESTART=0
STATUS_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --foreground|-f) FOREGROUND=1 ;;
    --restart)       RESTART=1 ;;
    --status)        STATUS_ONLY=1 ;;
    -h|--help)
      sed -n '2,27p' "$0"
      exit 0
      ;;
    *)
      fail "unknown flag: $arg"
      exit 2
      ;;
  esac
done

PORT="${VOICE_SIDECAR_PORT:-8765}"
HOST="${VOICE_SIDECAR_HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}"
RUNTIME_DIR="${ROOT_DIR}/.runtime"
PID_FILE="${RUNTIME_DIR}/voice_sidecar.pid"
LOG_FILE="${RUNTIME_DIR}/voice_sidecar.log"

mkdir -p "$RUNTIME_DIR"

# Default model cache lives on the host (NOT in the docker volume) so
# the model_cache docker volume can remain empty.
export VOICE_MODEL_DIR="${VOICE_MODEL_DIR:-${HOME}/.x-agent/models}"
mkdir -p "$VOICE_MODEL_DIR"
# 0700 -- weights are not secrets, but defence in depth.
chmod 700 "$VOICE_MODEL_DIR" 2>/dev/null || true

# ----- prereq: python venv -----
PYTHON="${ROOT_DIR}/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  fail "missing ${PYTHON}. Create the venv with 'python -m venv .venv && .venv/bin/pip install -e .'"
  exit 1
fi

# ----- helper: is the sidecar healthy? -----
# We curl /health AND grep for the sidecar's literal marker so a stray
# uvicorn serving the main app on the same port (whose SPA fallback
# returns HTML for /health) doesn't pass.
is_up() {
  curl -fsS -m 2 "${URL}/health" 2>/dev/null | grep -q '"ok"'
}

# ----- helper: read pidfile if alive -----
running_pid() {
  if [ -f "$PID_FILE" ]; then
    local pid; pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      printf '%s' "$pid"
      return 0
    fi
  fi
  return 1
}

# --- status ---
if [ "$STATUS_ONLY" -eq 1 ]; then
  if pid="$(running_pid)"; then
    ok  "voice sidecar PID $pid running"
  else
    warn "no recorded PID running"
  fi
  if is_up; then
    ok  "voice sidecar healthy at ${URL}/health"
  else
    warn "no healthy response from ${URL}/health"
  fi
  exit 0
fi

# --- restart: stop first ---
if [ "$RESTART" -eq 1 ]; then
  "$ROOT_DIR/scripts/stop_voice.sh" || true
fi

# --- skip if already running and healthy ---
if pid="$(running_pid)" && is_up; then
  ok "voice sidecar already running (PID $pid, ${URL})"
  exit 0
fi

# --- prereq: voice deps importable ---
if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import kokoro_onnx, faster_whisper, fastapi, uvicorn  # noqa
PY
then
  fail "voice deps missing in .venv. Run: '.venv/bin/pip install -e .'"
  exit 1
fi
ok "voice deps importable"

# --- port already taken by something else? ---
if is_up; then
  warn "${URL}/health already answers, but no PID we recorded. Re-using it."
  exit 0
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  fail "port ${PORT} is in use by another process. Free it or set VOICE_SIDECAR_PORT."
  exit 1
fi

info "starting voice sidecar on ${URL}"
dim  "model cache: ${VOICE_MODEL_DIR}"

# Pre-stage model files on the host using curl, which uses the system
# trust store (macOS Keychain, etc). Python's certifi-only TLS would
# fail behind corporate TLS interception, so we never rely on it.
# Idempotent: skips files already present.
if [ -x "$ROOT_DIR/scripts/download_models.sh" ]; then
  info "ensuring voice models are staged…"
  if ! "$ROOT_DIR/scripts/download_models.sh"; then
    warn "model download had errors; sidecar will start anyway but voice may be unavailable."
  fi
fi

export VOICE_SIDECAR_HOST="$HOST"
export VOICE_SIDECAR_PORT="$PORT"
# Disable HuggingFace telemetry; we don't talk to the hub when models
# are pre-staged, but truststore-fallback paths still might.
export HF_HUB_DISABLE_TELEMETRY=1

if [ "$FOREGROUND" -eq 1 ]; then
  exec "$PYTHON" "$ROOT_DIR/scripts/voice_server.py"
fi

# Background: write PID file, redirect logs.
nohup "$PYTHON" "$ROOT_DIR/scripts/voice_server.py" \
  >>"$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

# Wait for /health to come up (uvicorn boot + first ~0.5s for FastAPI).
ATTEMPTS=20
for i in $(seq 1 "$ATTEMPTS"); do
  if is_up; then
    ok "voice sidecar up (PID $NEW_PID, ${URL})"
    dim "logs: $LOG_FILE"
    exit 0
  fi
  if ! kill -0 "$NEW_PID" 2>/dev/null; then
    fail "sidecar died during boot; last 30 log lines:"
    tail -30 "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE"
    exit 1
  fi
  sleep 0.5
done

fail "sidecar boot timed out; last 30 log lines:"
tail -30 "$LOG_FILE" >&2 || true
exit 1
