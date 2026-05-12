"""Proxy client for a host-side voice sidecar.

When ``VOICE_REMOTE_URL`` is set, the FastAPI server forwards
``/api/voice/speak`` and ``/api/voice/transcribe`` to a sidecar that
runs on the *host* (see ``scripts/voice_server.py``).

Why? Containers often live behind corporate TLS interception (Zscaler,
Cisco Secure Access, etc) that the macOS keychain trusts but the
container's CA bundle does not. Moving model downloads to the host --
just like Ollama runs on the host on port 11434 -- side-steps that
problem entirely. The container needs zero outbound network access for
voice.

Design constraints (mirror the local-engine path in
:mod:`x_agent.voice.tts` / :mod:`x_agent.voice.stt`):

- The same request validation, rate limiting, size, duration, and
  content-type checks already run in ``server.py`` *before* we proxy.
- The proxy speaks plain HTTP over the loopback bridge
  (``host.docker.internal``); no auth is needed because the sidecar
  binds to localhost and is not reachable from outside the user's
  machine. This matches how Ollama is consumed.
- Errors from the sidecar are translated back to the same exception
  types the local engines raise (:class:`VoiceEngineUnavailable`,
  :class:`VoiceEngineError`), so the endpoint code path is identical.
- We never log raw audio bytes. Headers and status only.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from ..config import get_settings
from . import VoiceEngineError, VoiceEngineUnavailable

log = logging.getLogger(__name__)


# Generous client-side timeouts. The first /speak after a cold start
# can take a few seconds for the Kokoro session to initialise on the
# host; the first /transcribe similarly waits on faster-whisper model
# download. After warm-up, normal calls finish well under 5 s.
_CONNECT_TIMEOUT_S = 5.0
_READ_TIMEOUT_S = 180.0


def remote_url() -> Optional[str]:
    """Return the configured sidecar base URL, or ``None`` for local mode."""
    url = (get_settings().voice_remote_url or "").strip()
    if not url:
        return None
    # Defence-in-depth: refuse anything that isn't http/https. We
    # explicitly allow http because the sidecar is a loopback service
    # behind ``host.docker.internal`` -- TLS would only add friction.
    if not (url.startswith("http://") or url.startswith("https://")):
        raise VoiceEngineUnavailable(
            f"VOICE_REMOTE_URL must be http(s)://...; got {url!r}"
        )
    return url.rstrip("/")


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(
            connect=_CONNECT_TIMEOUT_S,
            read=_READ_TIMEOUT_S,
            write=_READ_TIMEOUT_S,
            pool=_READ_TIMEOUT_S,
        ),
        # The sidecar is local-only; no proxy traversal.
        trust_env=False,
    )


def _classify(status: int, detail: str) -> Exception:
    """Map a sidecar HTTP failure to our two exception types.

    503 / connect errors → :class:`VoiceEngineUnavailable` (so the
    endpoint returns 503 and the UI hides voice controls). Anything
    else 4xx/5xx → :class:`VoiceEngineError` (400 to the browser).
    """
    if status in (502, 503, 504):
        return VoiceEngineUnavailable(f"voice sidecar unavailable ({status}): {detail}")
    return VoiceEngineError(f"voice sidecar error ({status}): {detail}")


def health() -> dict[str, Any]:
    """Probe the sidecar's ``/health`` (best-effort).

    Returns a dict with ``ok`` plus the sidecar's reported state, or
    ``{"ok": False, "error": "..."}`` on failure. Never raises -- this
    is called from ``/api/health`` and must not fail the response.
    """
    base = remote_url()
    if not base:
        return {"ok": False, "error": "VOICE_REMOTE_URL not set"}
    try:
        with _client() as c:
            r = c.get(f"{base}/health", timeout=2.0)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                data.setdefault("ok", True)
                return data
            return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - any failure -> degraded
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def speak(
    text: str,
    *,
    voice: Optional[str] = None,
    speed: Optional[float] = None,
    lang: Optional[str] = None,
) -> bytes:
    """Forward a TTS request to the sidecar and return WAV bytes."""
    base = remote_url()
    if not base:
        raise VoiceEngineUnavailable("VOICE_REMOTE_URL is not set")

    payload: dict[str, Any] = {"text": text}
    if voice is not None:
        payload["voice"] = voice
    if speed is not None:
        payload["speed"] = float(speed)
    if lang is not None:
        payload["lang"] = lang

    try:
        with _client() as c:
            r = c.post(f"{base}/speak", json=payload)
    except httpx.HTTPError as exc:
        log.warning("voice.proxy.speak: connect failed: %s", exc)
        raise VoiceEngineUnavailable(
            f"failed to reach voice sidecar at {base}: {exc}"
        ) from exc

    if r.status_code != 200:
        detail = _detail(r)
        raise _classify(r.status_code, detail)

    body = r.content
    # The sidecar always returns audio/wav; a wrong content-type means
    # something is between us (a proxy?) -- refuse rather than hand bad
    # bytes to the browser.
    ct = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ct not in ("audio/wav", "audio/wave", "audio/x-wav"):
        raise VoiceEngineUnavailable(
            f"voice sidecar returned unexpected content-type: {ct!r}"
        )
    if not body or not body.startswith(b"RIFF"):
        raise VoiceEngineUnavailable("voice sidecar returned non-WAV body")
    return body


def transcribe(data: bytes, *, content_type: str) -> tuple[str, float]:
    """Forward an STT request to the sidecar.

    ``data`` is the already-validated audio body (size, content-type,
    and magic bytes were checked upstream in ``server.py``).
    ``content_type`` is the *validated* incoming type from the user
    upload; we forward it verbatim because the sidecar reuses the same
    :func:`validate_audio_upload` allowlist.
    """
    base = remote_url()
    if not base:
        raise VoiceEngineUnavailable("VOICE_REMOTE_URL is not set")
    if not data:
        raise VoiceEngineError("empty audio payload")

    files = {"audio": ("upload.bin", data, content_type or "application/octet-stream")}
    try:
        with _client() as c:
            r = c.post(f"{base}/transcribe", files=files)
    except httpx.HTTPError as exc:
        log.warning("voice.proxy.transcribe: connect failed: %s", exc)
        raise VoiceEngineUnavailable(
            f"failed to reach voice sidecar at {base}: {exc}"
        ) from exc

    if r.status_code != 200:
        detail = _detail(r)
        raise _classify(r.status_code, detail)

    try:
        body = r.json()
    except ValueError as exc:
        raise VoiceEngineUnavailable("voice sidecar returned non-JSON") from exc
    text = str(body.get("text", "")).strip()
    duration = float(body.get("duration_s", 0.0) or 0.0)
    return text, duration


def _detail(r: httpx.Response) -> str:
    """Pull a short error string out of a sidecar response, safely."""
    try:
        data = r.json()
        if isinstance(data, dict) and data.get("detail"):
            return str(data["detail"])[:300]
    except Exception:  # noqa: BLE001
        pass
    return (r.text or "")[:300]


__all__ = [
    "health",
    "remote_url",
    "speak",
    "transcribe",
]
