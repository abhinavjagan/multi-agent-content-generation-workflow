# syntax=docker/dockerfile:1.7
#
# Multi-stage image for the x-agent FastAPI server + bundled SPA + CLI.
# Stage 1 builds the React app with Node; stage 2 is the Python runtime that
# also serves the built SPA via FastAPI's StaticFiles mount.
#
# Hardening (per workspace codeguard rules):
#  - Pinned base images (no :latest)
#  - Multi-stage to keep the runtime image free of Node + build tools
#  - Non-root user (uid/gid 10001)
#  - HEALTHCHECK on the FastAPI liveness endpoint
#  - PYTHONDONTWRITEBYTECODE so .pyc files don't appear under /app at runtime
#    (which is mounted read-only in compose)


# -----------------------------------------------------------------------------
# Stage 1: build the React + Vite SPA
# -----------------------------------------------------------------------------
FROM node:20-bookworm-slim AS web

WORKDIR /web
# Note: do NOT set NODE_ENV=production here. With that set, `npm ci` skips
# devDependencies, but `tsc` and `vite` live in devDependencies and we need
# them for `npm run build`. Vite always emits a production bundle when
# `vite build` is invoked, regardless of NODE_ENV.
ENV CI=1

# Copy lockfile first so dependency installs are cached when only source
# changes between rebuilds.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build && ls -la dist


# -----------------------------------------------------------------------------
# Stage 2: Python runtime + bundled SPA
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is used by the HEALTHCHECK below; ca-certificates keeps outbound HTTPS
# (Ollama health probe, research providers when enabled) honest. ffmpeg is
# required by faster-whisper (the voice STT engine) to decode the webm/ogg/
# mp4 audio MediaRecorder produces; libsndfile1 is what soundfile (a
# transitive dep of kokoro-onnx examples) and Whisper's resamplers link
# against on Debian. espeak-ng is the phonemizer backend Kokoro depends on
# at runtime; without it TTS synthesis fails with a phonemizer error.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        curl ca-certificates ffmpeg libsndfile1 espeak-ng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Editable install (runtime extras only) so the `x-agent` console script
# defined in pyproject.toml resolves inside the container. Dev extras (pytest,
# mypy, ruff) deliberately live outside the runtime image — run them locally
# with `pip install -e .[dev]` or via CI. Copy the build inputs first so this
# layer is cached across source edits that don't touch pyproject.toml or src/.
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install -e .

# Built SPA goes where SPAStaticFiles in src/x_agent/server.py expects it
# (Path(__file__).resolve().parents[2] / "frontend" / "dist" -> /app/frontend/dist).
COPY --from=web /web/dist /app/frontend/dist

# Non-root user. /data/personas is the writable persona volume mount target;
# /data/models is the model cache volume (Kokoro ONNX + faster-whisper
# weights). /app stays read-only at runtime (root FS in compose is also
# read-only) -- everything mutable lives under /data or /tmp (tmpfs).
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data/personas /data/models \
 && chown -R app:app /app /data
USER app

# Defaults assume Ollama is on the host (compose adds the host-gateway alias
# so this resolves on Linux too). Override with --env OLLAMA_BASE_URL=... if
# Ollama is somewhere else.
ENV PERSONA_DIR=/data/personas \
    OLLAMA_BASE_URL=http://host.docker.internal:11434 \
    OLLAMA_MODEL=llama3:latest \
    EMBEDDING_MODEL=nomic-embed-text \
    VOICE_MODEL_DIR=/data/models

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "x_agent.server:app", "--host", "0.0.0.0", "--port", "8000"]
