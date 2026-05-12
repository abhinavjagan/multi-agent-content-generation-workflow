#!/usr/bin/env bash
# Pre-stage the x-agent voice models on the HOST using curl, which
# uses the OS trust store (macOS Keychain / system CA bundle on Linux).
# This sidesteps Python's certifi-only TLS in environments behind
# corporate TLS interception (Cisco Secure Access, Zscaler, etc).
#
# After this script finishes, both engines run fully offline:
#   - Kokoro-82M finds kokoro-v1.0.onnx + voices-v1.0.bin at VOICE_MODEL_DIR
#   - faster-whisper loads from VOICE_MODEL_DIR/faster-whisper-<size>/
#     (a directory layout that faster-whisper recognises via
#     `os.path.isdir(model_size_or_path)` — no HuggingFace call).
#
# Idempotent: re-running just confirms the staged files are present.
#
# Usage:
#   ./scripts/download_models.sh                # stage all models (default size: small.en)
#   VOICE_STT_MODEL=tiny.en ./scripts/download_models.sh
#   VOICE_MODEL_DIR=/tmp/models ./scripts/download_models.sh
#   ./scripts/download_models.sh --tts-only     # only Kokoro
#   ./scripts/download_models.sh --stt-only     # only faster-whisper
#   ./scripts/download_models.sh --force        # re-download even if files exist
#
# Storage footprint (small.en + Kokoro):
#   - kokoro-v1.0.onnx        ~310 MB
#   - voices-v1.0.bin         ~27  MB
#   - faster-whisper-small.en ~150 MB
#   = ~490 MB total under VOICE_MODEL_DIR

set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
ok()    { printf '%s\n' "${GREEN}✓${RESET} $*"; }
info()  { printf '%s\n' "${BOLD}→${RESET} $*"; }
warn()  { printf '%s\n' "${YELLOW}!${RESET} $*"; }
fail()  { printf '%s\n' "${RED}✗${RESET} $*" >&2; }
dim()   { printf '%s\n' "${DIM}$*${RESET}"; }

TTS_ONLY=0
STT_ONLY=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --tts-only) TTS_ONLY=1 ;;
    --stt-only) STT_ONLY=1 ;;
    --force)    FORCE=1 ;;
    -h|--help)
      sed -n '2,25p' "$0"
      exit 0
      ;;
    *)
      fail "unknown flag: $arg"
      exit 2
      ;;
  esac
done
if [ "$TTS_ONLY" -eq 1 ] && [ "$STT_ONLY" -eq 1 ]; then
  fail "--tts-only and --stt-only are mutually exclusive"
  exit 2
fi

# Resolve model dir (must be writable). ~/.x-agent/models matches the
# Settings.voice_model_dir default in src/x_agent/config.py.
VOICE_MODEL_DIR_RAW="${VOICE_MODEL_DIR:-${HOME}/.x-agent/models}"
# Expand a leading ~ (Settings does this via Path.expanduser; bash does
# not unless the value was a literal in the script). We deliberately
# match the literal '~' character here -- shellcheck SC2088 is a false
# positive in this case statement.
# shellcheck disable=SC2088
case "$VOICE_MODEL_DIR_RAW" in
  "~"|"~/"*) VOICE_MODEL_DIR_RAW="${HOME}${VOICE_MODEL_DIR_RAW#\~}" ;;
esac
MODEL_DIR="$VOICE_MODEL_DIR_RAW"
mkdir -p "$MODEL_DIR"
# 0700 -- weights aren't secrets, but defence in depth keeps stray
# users on a shared box from peeking.
chmod 700 "$MODEL_DIR" 2>/dev/null || true

STT_MODEL="${VOICE_STT_MODEL:-small.en}"

# Validate the model name against the same allow-list faster-whisper
# itself accepts, so we don't silently stage a directory that the
# engine will then reject at load time.
case "$STT_MODEL" in
  tiny|tiny.en|base|base.en|small|small.en|medium|medium.en|large-v2|large-v3|large-v3-turbo) ;;
  *)
    fail "unknown VOICE_STT_MODEL '$STT_MODEL'. Accepted: tiny/.en, base/.en, small/.en, medium/.en, large-v2, large-v3, large-v3-turbo."
    exit 2
    ;;
esac

# Require curl. We deliberately don't fall back to wget / python because
# the whole point of this script is to use the OS-native curl that
# trusts the system keychain.
if ! command -v curl >/dev/null 2>&1; then
  fail "curl not found on PATH. Install curl and re-run."
  exit 1
fi

# ----- helper: download a single URL to a destination if missing -----
fetch() {
  # fetch <label> <url> <dest_path> [min_bytes]
  local label="$1" url="$2" dst="$3" min_bytes="${4:-0}"
  if [ "$FORCE" -ne 1 ] && [ -f "$dst" ] && [ -s "$dst" ]; then
    local cur; cur="$(stat -f %z "$dst" 2>/dev/null || stat -c %s "$dst" 2>/dev/null || echo 0)"
    if [ "$cur" -ge "$min_bytes" ]; then
      ok "$label already staged ($(numfmt --to=iec "$cur" 2>/dev/null || echo "${cur} bytes"))"
      return 0
    fi
    warn "$label exists but is undersized ($cur bytes); re-downloading"
  fi
  info "fetching $label"
  dim "  url:  $url"
  dim "  dest: $dst"
  # --fail            -> non-zero exit on HTTP >=400
  # --location        -> follow 3xx redirects (HF -> CDN)
  # --retry 3         -> transient network blips
  # --retry-delay 2
  # --connect-timeout 15
  # --max-time 600    -> hard upper bound per file (10 min)
  # --progress-bar    -> compact bar instead of full meter
  local tmp="${dst}.part"
  mkdir -p "$(dirname "$dst")"
  if ! curl --fail --location --retry 3 --retry-delay 2 \
         --connect-timeout 15 --max-time 600 \
         --progress-bar \
         -o "$tmp" "$url"; then
    rm -f "$tmp"
    fail "download failed: $url"
    return 1
  fi
  local got; got="$(stat -f %z "$tmp" 2>/dev/null || stat -c %s "$tmp" 2>/dev/null || echo 0)"
  if [ "$got" -lt "$min_bytes" ]; then
    rm -f "$tmp"
    fail "$label: downloaded ${got} bytes < required ${min_bytes}; aborting."
    return 1
  fi
  mv "$tmp" "$dst"
  chmod 600 "$dst" 2>/dev/null || true
  ok "staged $label ($(numfmt --to=iec "$got" 2>/dev/null || echo "${got} bytes"))"
}

printf '\n%s\n' "${BOLD}x-agent · voice model pre-stage${RESET}"
dim "model dir:     $MODEL_DIR"
dim "stt model:     $STT_MODEL"
[ "$TTS_ONLY" -eq 1 ] && dim "scope:         TTS only (--tts-only)"
[ "$STT_ONLY" -eq 1 ] && dim "scope:         STT only (--stt-only)"
[ "$FORCE"    -eq 1 ] && dim "force:         re-downloading existing files"
printf '\n'

# ============================================================ Kokoro (TTS)
if [ "$STT_ONLY" -ne 1 ]; then
  # Pinned to the same v1.0 release the engine references in tts.py.
  # The model + voices files MUST match — bump together.
  KOKORO_BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
  fetch "Kokoro model" \
        "$KOKORO_BASE/kokoro-v1.0.onnx" \
        "$MODEL_DIR/kokoro-v1.0.onnx" \
        $((50 * 1024 * 1024))   # match _MIN_MODEL_BYTES in tts.py
  fetch "Kokoro voices" \
        "$KOKORO_BASE/voices-v1.0.bin" \
        "$MODEL_DIR/voices-v1.0.bin" \
        $((1 * 1024 * 1024))    # match _MIN_VOICES_BYTES in tts.py
fi

# ============================================================ faster-whisper (STT)
if [ "$TTS_ONLY" -ne 1 ]; then
  # faster-whisper's WhisperModel(local_dir) skips HuggingFace entirely
  # when os.path.isdir(model_size_or_path) is true. Stage the same files
  # huggingface_hub.snapshot_download() would pull for Systran/faster-whisper-<size>.
  HF_BASE="https://huggingface.co/Systran/faster-whisper-${STT_MODEL}/resolve/main"
  STT_DIR="$MODEL_DIR/faster-whisper-${STT_MODEL}"
  mkdir -p "$STT_DIR"

  # `model.bin` is the only non-trivial file (~150 MB for small.en).
  # The smaller files are required for tokenisation/config; faster-whisper
  # refuses to load if any are missing.
  fetch "faster-whisper model.bin (${STT_MODEL})" \
        "$HF_BASE/model.bin" \
        "$STT_DIR/model.bin" \
        $((10 * 1024 * 1024))
  fetch "faster-whisper config.json" \
        "$HF_BASE/config.json" \
        "$STT_DIR/config.json" \
        100
  fetch "faster-whisper tokenizer.json" \
        "$HF_BASE/tokenizer.json" \
        "$STT_DIR/tokenizer.json" \
        1000
  fetch "faster-whisper vocabulary.txt" \
        "$HF_BASE/vocabulary.txt" \
        "$STT_DIR/vocabulary.txt" \
        100
  # preprocessor_config.json isn't always present on every model; we
  # try to fetch it but treat 404 as non-fatal. faster-whisper falls
  # back to defaults if it's absent.
  if ! fetch "faster-whisper preprocessor_config.json" \
             "$HF_BASE/preprocessor_config.json" \
             "$STT_DIR/preprocessor_config.json" \
             10 2>/dev/null; then
    warn "preprocessor_config.json not available for ${STT_MODEL}; using engine defaults"
  fi
fi

# ============================================================ summary
printf '\n%s\n' "${BOLD}staged files under ${MODEL_DIR}:${RESET}"
( cd "$MODEL_DIR" && find . -type f -not -name '*.part' -print0 | xargs -0 ls -lh ) | awk '{printf "  %7s  %s\n", $5, $NF}'
TOTAL=$(du -sh "$MODEL_DIR" 2>/dev/null | awk '{print $1}')
ok "voice models ready (${TOTAL:-?} on disk). The sidecar will load them next start."
