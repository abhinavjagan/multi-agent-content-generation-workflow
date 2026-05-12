"""Host-side voice sidecar for x-agent.

A tiny FastAPI service that owns the Kokoro-82M (TTS) and faster-whisper
(STT) engines. It lives on the host -- not inside the container -- so
that model downloads use the host's network and trust chain (same as
Ollama on :11434).

The container's ``/api/voice/*`` endpoints forward to this sidecar
when ``VOICE_REMOTE_URL`` is set (see ``src/x_agent/voice/proxy.py``).

By default we bind ``127.0.0.1:8765``:

- Loopback-only: not reachable from the LAN. Matches Ollama's posture.
- No auth: also matches Ollama. This is a single-user local sidecar.
- Audio is never persisted (see :class:`~x_agent.voice.stt.WhisperSTT`).
- All limits (size, duration, text length, rate) are re-enforced here
  so the sidecar is safe to call directly from anywhere on the host,
  not only via the container.

Run with:

    .venv/bin/python -m scripts.voice_server
    # or via the launcher:
    ./scripts/start_voice.sh
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Belt-and-suspenders TLS trust: route Python's ``ssl`` module through
# the host OS trust store (macOS Keychain, Windows cert store,
# Linux p11-kit) instead of certifi's bundled CA list. This is a no-op
# on machines without corporate TLS interception, but on machines that
# DO have it -- and where the OS keychain trusts the MITM root --
# huggingface_hub fallbacks (and any other HTTPS the sidecar does) will
# work without us shipping a CA bundle.
# Pre-staged models via scripts/download_models.sh already side-step
# this entirely, so the import is treated as optional.
try:
    import truststore  # type: ignore[import-untyped]

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover - optional dep
    pass

# Make the package importable when invoked as a plain script
# (``python scripts/voice_server.py``).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from x_agent.config import get_settings
from x_agent.voice import (
    KokoroTTS,
    VoiceEngineError,
    VoiceEngineUnavailable,
    WhisperSTT,
)
from x_agent.voice.security import RateLimiter, client_key, validate_audio_upload

log = logging.getLogger("x_agent.voice_sidecar")

_VOICE_NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


app = FastAPI(
    title="x-agent voice sidecar",
    version="0.1.0",
    docs_url=None,        # No OpenAPI UI; this is a private loopback service.
    redoc_url=None,
    openapi_url=None,
)


_rate_limiter: Optional[RateLimiter] = None


def _get_rate_limiter() -> RateLimiter:
    """Lazily build the rate limiter using the current settings."""
    global _rate_limiter
    if _rate_limiter is None:
        s = get_settings()
        _rate_limiter = RateLimiter(
            max_per_window=s.voice_rate_limit_per_5min,
            window_seconds=300,
        )
    return _rate_limiter


def _enforce_rate_limit(request: Request) -> None:
    s = get_settings()
    if not s.voice_enabled:
        raise HTTPException(status_code=503, detail="voice pipeline is disabled")
    host = request.client.host if request.client else None
    allowed, retry_after = _get_rate_limiter().allow(client_key(host))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="voice rate limit exceeded",
            headers={"Retry-After": f"{int(retry_after) + 1}"},
        )


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    voice: Optional[str] = Field(
        default=None, max_length=40, pattern=r"^[a-z]{2}_[a-z0-9_]+$"
    )
    speed: Optional[float] = Field(default=None, ge=0.5, le=2.0)
    lang: Optional[str] = Field(
        default=None, max_length=10, pattern=r"^[a-z]{2}-[a-z]{2}$"
    )


class TranscribeResponse(BaseModel):
    text: str
    duration_s: float
    model: str


@app.get("/health")
def health() -> dict[str, object]:
    """Readiness probe. Mirrors the ``voice`` block in the main app."""
    s = get_settings()
    try:
        tts_ready = KokoroTTS.get().is_ready()
    except Exception:  # noqa: BLE001
        tts_ready = False
    try:
        stt_ready = WhisperSTT.get().is_ready()
    except Exception:  # noqa: BLE001
        stt_ready = False
    return {
        "ok": True,
        "enabled": bool(s.voice_enabled),
        "stt_model": s.voice_stt_model,
        "tts_voice": s.voice_tts_voice,
        "tts_lang": s.voice_tts_lang,
        "stt_ready": stt_ready,
        "tts_ready": tts_ready,
        "model_dir": str(Path(s.voice_model_dir).expanduser()),
    }


@app.post("/speak")
async def speak(req: SpeakRequest, request: Request) -> Response:
    """Synthesize ``text`` to WAV with Kokoro-82M."""
    import asyncio

    _enforce_rate_limit(request)
    s = get_settings()
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    if len(text) > s.voice_tts_max_chars:
        raise HTTPException(
            status_code=413,
            detail=f"text too long (max {s.voice_tts_max_chars} chars)",
        )
    try:
        wav = await asyncio.to_thread(
            KokoroTTS.get().synthesize,
            text,
            voice=req.voice,
            speed=req.speed,
            lang=req.lang,
        )
    except VoiceEngineUnavailable as exc:
        log.warning("voice_sidecar.speak unavailable: %s", exc)
        raise HTTPException(
            status_code=503, detail=f"TTS engine unavailable: {exc}"
        ) from exc
    except VoiceEngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=wav,
        media_type="audio/wav",
        headers=_VOICE_NO_STORE_HEADERS,
    )


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    request: Request,
    audio: UploadFile = File(...),
) -> TranscribeResponse:
    """Transcribe an uploaded audio blob via faster-whisper."""
    import asyncio

    _enforce_rate_limit(request)
    s = get_settings()
    cap = int(s.voice_max_audio_bytes)

    body = await audio.read(cap + 1)
    if len(body) > cap:
        raise HTTPException(
            status_code=413,
            detail=f"audio body too large (>{cap} bytes)",
        )
    suffix = validate_audio_upload(
        content_type=audio.content_type or "",
        body=body,
        max_bytes=cap,
    )
    try:
        text, duration = await asyncio.to_thread(
            WhisperSTT.get().transcribe, body, suffix=suffix
        )
    except VoiceEngineUnavailable as exc:
        log.warning("voice_sidecar.transcribe unavailable: %s", exc)
        raise HTTPException(
            status_code=503, detail=f"STT engine unavailable: {exc}"
        ) from exc
    except VoiceEngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if duration > s.voice_max_audio_seconds:
        raise HTTPException(
            status_code=413,
            detail=(
                f"audio duration too long ({duration:.1f}s > "
                f"{s.voice_max_audio_seconds}s)"
            ),
        )
    return TranscribeResponse(
        text=text,
        duration_s=float(duration),
        model=s.voice_stt_model,
    )


def main() -> None:
    """Run the sidecar via uvicorn. Loopback-only by default."""
    import uvicorn

    host = os.environ.get("VOICE_SIDECAR_HOST", "127.0.0.1")
    port = int(os.environ.get("VOICE_SIDECAR_PORT", "8765"))
    log_level = os.environ.get("VOICE_SIDECAR_LOG_LEVEL", "info")

    # If the operator overrides VOICE_SIDECAR_HOST to a non-loopback
    # address, log a warning so they realise the sidecar is now LAN-
    # reachable. We don't block it -- some users intentionally bind
    # to 0.0.0.0 inside Docker network namespaces.
    if host not in ("127.0.0.1", "::1", "localhost"):
        log.warning(
            "voice_sidecar: binding to non-loopback host %r -- this exposes "
            "the sidecar to your network. Set VOICE_SIDECAR_HOST=127.0.0.1 "
            "to restrict to loopback.",
            host,
        )

    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        # Single worker; the engines are process-wide singletons.
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()
