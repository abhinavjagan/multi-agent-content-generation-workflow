"""Upload validation and rate limiting for ``/api/voice/*`` endpoints.

Two surfaces:

- :func:`validate_audio_upload` -- enforces size cap, content-type
  allowlist, and a magic-byte sniff. Returns the inferred file extension
  the STT layer should use when writing to a temp file. Raises
  :class:`fastapi.HTTPException` with the appropriate 4xx code on rejection.
- :class:`RateLimiter` -- in-process per-IP token bucket. The voice
  endpoints are local-only, but we still bound them so a buggy client
  loop can't pin a CPU core indefinitely.

We deliberately keep this code free of any third-party dependencies
(no libmagic, no python-magic): the allowlist is tiny and the magic
bytes are well-known. python-magic would require ``libmagic1`` in the
container; not worth it for four formats.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

from fastapi import HTTPException

log = logging.getLogger(__name__)

# Allowed (content_type -> file extension) pairs. The extensions match
# what MediaRecorder produces in modern browsers, plus WAV for callers
# that pre-encode (e.g. our smoke script). We deliberately do NOT accept
# ``audio/*`` wildcards -- only this four-entry allowlist.
_ALLOWED: dict[str, str] = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".mp4",
    "audio/aac": ".m4a",
    "audio/mpeg": ".mp3",
}

# Magic-byte signatures. Each entry maps a friendly format name to a
# list of (offset, signature_bytes) tuples; the upload is accepted if
# ALL signatures for at least one entry match. Sniffing is bounded to
# the first 64 bytes of the payload regardless of size.
_SIGNATURES: dict[str, list[tuple[int, bytes]]] = {
    "webm": [(0, b"\x1a\x45\xdf\xa3")],
    "ogg":  [(0, b"OggS")],
    "wav":  [(0, b"RIFF"), (8, b"WAVE")],
    # ISO BMFF (mp4/m4a) puts a "ftyp" box at byte 4. The first 4 bytes
    # are the box length and are variable; we only check the literal.
    "mp4":  [(4, b"ftyp")],
    "mp3":  [(0, b"ID3")],  # ID3v2 tag; bare-frame MP3 is rejected.
}

# Map content-type (after normalisation) to the magic-byte family it
# should sniff as. Keeps the matcher tight: a request claiming
# ``audio/wav`` MUST have RIFF/WAVE bytes -- claiming wav but sending
# a webm body is rejected even though both are technically allow-listed.
_TYPE_TO_FAMILY: dict[str, str] = {
    "audio/webm": "webm",
    "audio/ogg":  "ogg",
    "audio/wav":  "wav",
    "audio/wave": "wav",
    "audio/x-wav": "wav",
    "audio/mp4":  "mp4",
    "audio/aac":  "mp4",
    "audio/mpeg": "mp3",
}

_SNIFF_BYTES = 64


def _matches(family: str, head: bytes) -> bool:
    for offset, sig in _SIGNATURES.get(family, []):
        if not head[offset : offset + len(sig)] == sig:
            return False
    return bool(_SIGNATURES.get(family))


def validate_audio_upload(
    *,
    content_type: Optional[str],
    body: bytes,
    max_bytes: int,
) -> str:
    """Validate an audio upload.

    Returns the inferred file extension (e.g. ``".webm"``). Raises
    :class:`HTTPException` 413 / 415 / 422 on rejection.
    """
    if not isinstance(body, (bytes, bytearray)):
        raise HTTPException(status_code=422, detail="audio body must be bytes")
    if not body:
        raise HTTPException(status_code=422, detail="empty audio body")
    if len(body) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"audio body too large ({len(body)} > {max_bytes} bytes)",
        )

    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct not in _ALLOWED:
        raise HTTPException(
            status_code=415,
            detail=(
                "unsupported audio Content-Type; expected one of "
                f"{sorted(set(_ALLOWED.keys()))}"
            ),
        )

    family = _TYPE_TO_FAMILY[ct]
    head = bytes(body[:_SNIFF_BYTES])
    if not _matches(family, head):
        # Defence-in-depth: a caller can lie about Content-Type to bypass
        # ffmpeg discovery quirks. Reject the upload.
        raise HTTPException(
            status_code=415,
            detail=(
                f"audio body does not look like the declared format "
                f"({ct}); magic bytes mismatch"
            ),
        )

    return _ALLOWED[ct]


# ---------------------------------------------------------------- rate limit


class RateLimiter:
    """Per-key sliding-window rate limiter.

    Lock-protected; safe under concurrent FastAPI workers in the same
    process (uvicorn defaults to a single worker; gunicorn with multiple
    workers would need a shared store -- not supported here because this
    is a single-user local app).
    """

    def __init__(self, *, max_per_window: int, window_seconds: int) -> None:
        self._max = int(max_per_window)
        self._window = int(window_seconds)
        # key -> deque[float] of monotonic timestamps
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """Return ``(allowed, retry_after_s)`` for ``key``.

        ``retry_after_s`` is 0 when allowed; otherwise it's the number of
        seconds the caller should wait before the oldest in-window
        request falls off.
        """
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            q = self._buckets.get(key)
            if q is None:
                q = deque()
                self._buckets[key] = q
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self._max:
                retry = max(0.0, q[0] + self._window - now)
                return False, retry
            q.append(now)
            return True, 0.0

    def reset(self) -> None:
        """Clear all bucket state. Used by tests."""
        with self._lock:
            self._buckets.clear()


def client_key(client_host: Optional[str]) -> str:
    """Stable key for the limiter from the incoming request.

    Falls back to ``"unknown"`` when the request didn't expose a remote
    host (test client, unix socket, etc).
    """
    return (client_host or "unknown").strip() or "unknown"


__all__ = [
    "RateLimiter",
    "client_key",
    "validate_audio_upload",
]
