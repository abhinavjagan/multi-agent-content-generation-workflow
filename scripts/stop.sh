#!/usr/bin/env bash
# Stop the x-agent container. Pass --wipe to also delete the persona volume
# (this nukes ~/.x-agent/personas in the container — irreversible).
#
# Usage:
#   ./scripts/stop.sh            # graceful shutdown
#   ./scripts/stop.sh --wipe     # shutdown + delete persona_data volume

set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
ok()   { printf '%s\n' "${GREEN}✓${RESET} $*"; }
warn() { printf '%s\n' "${YELLOW}!${RESET} $*"; }
fail() { printf '%s\n' "${RED}✗${RESET} $*" >&2; }

DOCKER_COMPOSE="docker compose"
if ! docker compose version >/dev/null 2>&1; then
  if command -v docker-compose >/dev/null 2>&1; then
    DOCKER_COMPOSE="docker-compose"
  else
    fail "docker compose v2 not found"
    exit 1
  fi
fi

WIPE=0
for arg in "$@"; do
  case "$arg" in
    --wipe) WIPE=1 ;;
    -h|--help)
      sed -n '2,9p' "$0"
      exit 0
      ;;
    *)
      fail "unknown flag: $arg"
      exit 2
      ;;
  esac
done

if [ "$WIPE" -eq 1 ]; then
  warn "wiping persona_data volume — this deletes every saved persona."
  $DOCKER_COMPOSE down -v
  ok "stopped and removed volume"
else
  $DOCKER_COMPOSE down
  ok "stopped; persona_data preserved"
fi
