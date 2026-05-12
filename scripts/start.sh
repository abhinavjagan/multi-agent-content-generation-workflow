#!/usr/bin/env bash
# x-agent bootstrap — one command to set up Ollama (on host), the FastAPI
# server (in Docker), and the bundled SPA, then poll /api/health until ready.
#
# Safe to re-run; every step is idempotent. Pass --pull-only to refresh the
# Ollama models without rebuilding the container, or --no-build to skip the
# image rebuild.
#
# Usage:
#   ./scripts/start.sh              # pull models, build, start, wait for health
#   ./scripts/start.sh --no-build   # don't rebuild the image
#   ./scripts/start.sh --pull-only  # only pull Ollama models, then exit

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
ok()    { printf '%s\n' "${GREEN}✓${RESET} $*"; }
info()  { printf '%s\n' "${BOLD}→${RESET} $*"; }
warn()  { printf '%s\n' "${YELLOW}!${RESET} $*"; }
fail()  { printf '%s\n' "${RED}✗${RESET} $*" >&2; }
dim()   { printf '%s\n' "${DIM}$*${RESET}"; }

NO_BUILD=0
PULL_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-build)  NO_BUILD=1 ;;
    --pull-only) PULL_ONLY=1 ;;
    -h|--help)
      sed -n '2,11p' "$0"
      exit 0
      ;;
    *)
      fail "unknown flag: $arg"
      exit 2
      ;;
  esac
done

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3:latest}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-nomic-embed-text}"

printf '\n%s\n' "${BOLD}x-agent · local content generation${RESET}"
printf '%s\n\n' "${DIM}local-only · no posting · personas live in ~/.x-agent/personas${RESET}"

# ----------------------------------------------------------------- prereq: docker
if ! command -v docker >/dev/null 2>&1; then
  fail "docker not found on PATH. Install Docker Desktop and re-run."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  fail "docker is installed but not running. Start Docker Desktop and re-run."
  exit 1
fi
ok "docker is running"

DOCKER_COMPOSE="docker compose"
if ! docker compose version >/dev/null 2>&1; then
  if command -v docker-compose >/dev/null 2>&1; then
    DOCKER_COMPOSE="docker-compose"
    warn "using legacy docker-compose binary"
  else
    fail "docker compose v2 not found. Update Docker Desktop or install docker-compose."
    exit 1
  fi
fi

# ----------------------------------------------------------------- prereq: ollama
if ! command -v ollama >/dev/null 2>&1; then
  fail "ollama not found on PATH. Install from https://ollama.com or 'brew install ollama'."
  exit 1
fi
ok "ollama binary found ($(ollama --version 2>/dev/null | head -n1 || echo unknown))"

# On Linux the daemon isn't always auto-started by the installer.
if ! curl -fsS -m 2 "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
  case "$(uname -s)" in
    Darwin)
      warn "Ollama API not reachable at $OLLAMA_BASE_URL. Open the Ollama.app or run 'ollama serve' in another terminal."
      ;;
    Linux)
      warn "Ollama API not reachable at $OLLAMA_BASE_URL."
      warn "Start it with: ${BOLD}ollama serve${RESET} (or: systemctl --user start ollama)."
      ;;
    *)
      warn "Ollama API not reachable at $OLLAMA_BASE_URL. Start the Ollama daemon."
      ;;
  esac
  warn "Re-run ./scripts/start.sh once Ollama is up."
  exit 1
fi
ok "ollama API reachable at $OLLAMA_BASE_URL"

pull_if_missing() {
  local model="$1"
  if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -Fxq "$model"; then
    ok "$model already pulled"
  else
    info "pulling $model (one-time, may take a few minutes)…"
    ollama pull "$model"
    ok "pulled $model"
  fi
}

pull_if_missing "$OLLAMA_MODEL"
pull_if_missing "$EMBEDDING_MODEL"

if [ "$PULL_ONLY" -eq 1 ]; then
  ok "pull-only complete; skipping Docker build/start."
  exit 0
fi

# ----------------------------------------------------------------- prereq: voice sidecar
# Voice (Kokoro TTS + faster-whisper STT) lives on the HOST -- same pattern
# as Ollama -- so the container never needs to download from HuggingFace /
# GitHub behind corporate TLS interception. Skipped only if voice is
# explicitly disabled in .env.
VOICE_ENABLED_VAL="${VOICE_ENABLED:-true}"
if [ -f .env ] && grep -E '^VOICE_ENABLED=' .env >/dev/null 2>&1; then
  VOICE_ENABLED_VAL="$(grep -E '^VOICE_ENABLED=' .env | tail -n1 | cut -d= -f2)"
fi
VOICE_ENABLED_LC="$(printf '%s' "$VOICE_ENABLED_VAL" | tr '[:upper:]' '[:lower:]')"
case "$VOICE_ENABLED_LC" in
  false|0|no) START_VOICE=0 ;;
  *)          START_VOICE=1 ;;
esac

if [ "$START_VOICE" -eq 1 ]; then
  if [ -x ./scripts/start_voice.sh ]; then
    info "starting voice sidecar (Kokoro TTS + faster-whisper STT) on host…"
    if ./scripts/start_voice.sh; then
      ok "voice sidecar ready at http://127.0.0.1:8765"
    else
      warn "voice sidecar failed to start; the app will still run but voice will be unavailable."
      warn "see .runtime/voice_sidecar.log for details, or set VOICE_ENABLED=false in .env."
    fi
  else
    warn "scripts/start_voice.sh not found / not executable; skipping voice sidecar."
  fi
else
  dim "VOICE_ENABLED=false -- skipping voice sidecar."
fi

# ----------------------------------------------------------------- .env scaffold
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    ok "created .env from .env.example (defaults work; edit for custom config)"
  else
    warn ".env.example missing; skipping .env scaffold"
  fi
else
  ok ".env already present"
fi

# ----------------------------------------------------------------- build & start
if [ "$NO_BUILD" -eq 1 ]; then
  info "starting (without rebuild)…"
  $DOCKER_COMPOSE up -d
else
  info "building & starting (this prints a lot the first time)…"
  $DOCKER_COMPOSE up --build -d
fi
ok "container started; tailing health…"

# ----------------------------------------------------------------- wait for health
HEALTH_URL="http://localhost:8000/api/health"
ATTEMPTS=40   # ~2 minutes at 3s interval
for i in $(seq 1 "$ATTEMPTS"); do
  if curl -fsS -m 2 "$HEALTH_URL" >/dev/null 2>&1; then
    ok "FastAPI is up: $HEALTH_URL"
    break
  fi
  if [ "$i" -eq "$ATTEMPTS" ]; then
    fail "health check never went green; last 50 log lines:"
    $DOCKER_COMPOSE logs --tail=50 app || true
    exit 1
  fi
  printf "  ${DIM}waiting for health… (%d/%d)${RESET}\r" "$i" "$ATTEMPTS"
  sleep 3
done
printf '\n'

printf '\n%s\n' "${BOLD}x-agent is ready.${RESET}"
printf '  %s  %s\n' "Web UI:" "${BOLD}http://localhost:8000${RESET}"
printf '  %s  %s\n' "Health:" "$HEALTH_URL"
printf '  %s  %s\n' "Stop:  " "./scripts/stop.sh"
printf '  %s  %s\n' "Logs:  " "$DOCKER_COMPOSE logs -f app"
printf '\n%s\n' "${DIM}Personas live on a docker volume; back them up with 'docker volume export persona_data'.${RESET}"
if [ "$START_VOICE" -eq 1 ]; then
  printf '%s\n' "${DIM}Voice (TTS + STT) runs in a host-side sidecar on :8765 (logs:${RESET}"
  printf '%s\n' "${DIM}  .runtime/voice_sidecar.log). First /api/voice/* request downloads${RESET}"
  printf '%s\n' "${DIM}  ~350 MB into ~/.x-agent/models. Set VOICE_ENABLED=false to disable.${RESET}"
fi
