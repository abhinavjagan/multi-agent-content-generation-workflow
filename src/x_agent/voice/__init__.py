"""Voice pipeline: local TTS (Kokoro-82M ONNX) + STT (faster-whisper).

This package is intentionally a thin wrapper around two third-party
inference libraries. It exposes two lazy-loaded singletons -- :class:`KokoroTTS`
and :class:`WhisperSTT` -- plus the upload validation + rate-limit helpers
in :mod:`x_agent.voice.security`.

Design constraints:

- **All-local.** No model is downloaded from anywhere except first-time
  HTTPS pulls of the ONNX weights to ``VOICE_MODEL_DIR``. After that the
  pipeline is offline.
- **Lazy.** Importing this package never touches PyTorch / ONNX -- the
  engines load on first call. This keeps ``import x_agent.server`` cheap
  and lets the FastAPI worker start even if the model weights are missing
  or corrupt (endpoints simply return 503).
- **Audio is never persisted.** :func:`WhisperSTT.transcribe` writes the
  upload to a ``NamedTemporaryFile`` under ``/tmp`` (tmpfs in compose),
  deletes it after transcription, and never logs the content.
- **Bounded.** Size + duration caps live in :mod:`x_agent.voice.security`
  and are enforced before any decoder runs.
"""

from __future__ import annotations

__all__ = [
    "KokoroTTS",
    "WhisperSTT",
    "VoiceEngineError",
    "VoiceEngineUnavailable",
]


class VoiceEngineError(RuntimeError):
    """Raised when a voice engine fails at runtime (decode error, OOM, etc)."""


class VoiceEngineUnavailable(RuntimeError):
    """Raised when a voice engine cannot be initialised at all.

    The FastAPI layer converts this to HTTP 503 so the UI can hide voice
    controls without breaking the typed-answer flow.
    """


# Re-export the singletons. Importing them does NOT trigger engine load --
# the modules themselves only construct heavy objects inside ``get()``.
from .stt import WhisperSTT  # noqa: E402
from .tts import KokoroTTS  # noqa: E402
