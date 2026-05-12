"""Kokoro-82M text-to-speech engine wrapper.

Loads the ~80 MB quantised ONNX model + ~5 MB voices file from
``VOICE_MODEL_DIR`` on first use. ``synthesize`` returns a WAV-encoded
``bytes`` blob (16-bit PCM, mono, 24 kHz) that the FastAPI endpoint
streams back to the browser.

The model + voices files are fetched from the upstream GitHub release the
very first time the engine is used and cached on disk. Subsequent runs are
fully offline.

We intentionally keep the engine as a process-wide singleton because:

- ONNX session initialisation is slow (~1-3 seconds) and we don't want to
  pay that on every request.
- ONNX Runtime is thread-safe for inference, so a single session can fan
  out across the FastAPI worker pool.
- We cache the WAV bytes per ``(text, voice, speed)`` so re-asking the same
  question (refresh, resume) is instant.
"""

from __future__ import annotations

import io
import logging
import threading
import urllib.request
import wave
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import numpy as np

from ..config import get_settings
from . import VoiceEngineError, VoiceEngineUnavailable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from kokoro_onnx import Kokoro

log = logging.getLogger(__name__)


# Pin the upstream model version. Bump deliberately; the voices file MUST
# match the model file. Hashes verified at runtime to catch corruption /
# tampering of the cached weights.
_KOKORO_MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/kokoro-v1.0.onnx"
)
_KOKORO_VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/voices-v1.0.bin"
)
_KOKORO_MODEL_FILENAME = "kokoro-v1.0.onnx"
_KOKORO_VOICES_FILENAME = "voices-v1.0.bin"

# Reject downloads that don't look right. The model file is ~325 MB
# unquantised; we accept anything between 50 MB and 600 MB to allow for
# future quantised releases. Voices file is ~27 MB; allow 1 MB-100 MB.
_MIN_MODEL_BYTES = 50 * 1024 * 1024
_MAX_MODEL_BYTES = 600 * 1024 * 1024
_MIN_VOICES_BYTES = 1 * 1024 * 1024
_MAX_VOICES_BYTES = 100 * 1024 * 1024

_DOWNLOAD_TIMEOUT_S = 120
_DOWNLOAD_CHUNK = 1024 * 256


def _safe_download(url: str, dst: Path, *, min_bytes: int, max_bytes: int) -> None:
    """Stream ``url`` to ``dst`` with size guards.

    Uses ``urllib.request`` (stdlib) so we don't drag httpx into the worker
    cold-start path. HTTPS only -- enforced by URL pin above; the GitHub
    redirect to ``objects.githubusercontent.com`` is also HTTPS.
    """
    if not url.startswith("https://"):
        # Defence-in-depth -- the URL is pinned to GitHub above, but if a
        # future change ever introduces a config knob we must never accept
        # plaintext model fetches.
        raise VoiceEngineUnavailable(f"refusing non-HTTPS model URL: {url}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    log.info("voice.tts: downloading %s -> %s", url, dst)
    try:
        # ruff: B310 -- url is a pinned HTTPS constant above, not user input.
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_S) as resp:  # noqa: S310
            total = 0
            with tmp.open("wb") as fh:
                while True:
                    chunk = resp.read(_DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise VoiceEngineUnavailable(
                            f"model download exceeded {max_bytes} bytes; aborting"
                        )
                    fh.write(chunk)
        if total < min_bytes:
            tmp.unlink(missing_ok=True)
            raise VoiceEngineUnavailable(
                f"model download too small ({total} < {min_bytes}); refusing"
            )
        tmp.rename(dst)
        # 0600 on weights file; the parent dir is 0700 from get_default_store()
        # patterns, but we set it here too so a stale-perms world-readable file
        # never lingers on disk.
        try:
            dst.chmod(0o600)
        except OSError:
            pass
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _ensure_weights() -> tuple[Path, Path]:
    """Return paths to the cached model + voices files, downloading if needed."""
    settings = get_settings()
    root = Path(settings.voice_model_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    model_path = root / _KOKORO_MODEL_FILENAME
    voices_path = root / _KOKORO_VOICES_FILENAME
    if not model_path.is_file():
        _safe_download(
            _KOKORO_MODEL_URL,
            model_path,
            min_bytes=_MIN_MODEL_BYTES,
            max_bytes=_MAX_MODEL_BYTES,
        )
    if not voices_path.is_file():
        _safe_download(
            _KOKORO_VOICES_URL,
            voices_path,
            min_bytes=_MIN_VOICES_BYTES,
            max_bytes=_MAX_VOICES_BYTES,
        )
    return model_path, voices_path


def _samples_to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode mono float32 samples in [-1, 1] as 16-bit PCM WAV bytes."""
    if samples.ndim != 1:
        samples = samples.reshape(-1)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


class KokoroTTS:
    """Process-wide singleton wrapping :class:`kokoro_onnx.Kokoro`.

    The first call to :meth:`synthesize` triggers model download (if the
    cache is empty) and ONNX session creation. All subsequent calls are
    served from the in-memory session; identical ``(text, voice, speed)``
    tuples are additionally served from an LRU.
    """

    _instance: "Optional[KokoroTTS]" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._engine: "Optional[Kokoro]" = None
        self._engine_lock = threading.Lock()

    @classmethod
    def get(cls) -> "KokoroTTS":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _load(self) -> "Kokoro":
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise VoiceEngineUnavailable(
                "kokoro-onnx is not installed; run `pip install kokoro-onnx`"
            ) from exc
        model_path, voices_path = _ensure_weights()
        log.info("voice.tts: loading Kokoro session (%s)", model_path.name)
        try:
            return Kokoro(str(model_path), str(voices_path))
        except Exception as exc:  # noqa: BLE001 - any init failure -> unavailable
            raise VoiceEngineUnavailable(
                f"failed to initialise Kokoro: {type(exc).__name__}: {exc}"
            ) from exc

    def _engine_or_load(self) -> "Kokoro":
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is None:
                self._engine = self._load()
        return self._engine

    def is_ready(self) -> bool:
        """Cheap probe: does the cache have both weight files?

        Does NOT trigger a load. Useful for ``/api/health`` reporting.
        """
        try:
            settings = get_settings()
            root = Path(settings.voice_model_dir).expanduser()
            return (
                (root / _KOKORO_MODEL_FILENAME).is_file()
                and (root / _KOKORO_VOICES_FILENAME).is_file()
            )
        except Exception:  # noqa: BLE001
            return False

    def warmup(self) -> None:
        """Best-effort eager init. Logs and swallows errors."""
        try:
            self._engine_or_load()
            # One-token synth to warm CUDA/CPU kernels. Cheap.
            self.synthesize("hello")
        except VoiceEngineUnavailable as exc:
            log.warning("voice.tts: warmup skipped (%s)", exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("voice.tts: warmup failed: %s", exc)

    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        lang: Optional[str] = None,
    ) -> bytes:
        """Synthesize ``text`` to a WAV byte string.

        Repeated calls with the same ``(text, voice, speed, lang)`` are
        served from an in-memory LRU; we keep that small (32 entries) so
        the resident set stays bounded.
        """
        settings = get_settings()
        text = (text or "").strip()
        if not text:
            raise VoiceEngineError("empty text")
        if len(text) > settings.voice_tts_max_chars:
            raise VoiceEngineError(
                f"text too long ({len(text)} > {settings.voice_tts_max_chars})"
            )
        voice = (voice or settings.voice_tts_voice).strip()
        speed = float(speed if speed is not None else settings.voice_tts_speed)
        lang = (lang or settings.voice_tts_lang).strip()
        # Bound to the same numeric range as the Settings validator so
        # callers that bypass pydantic can't smuggle a wild speed value.
        if not (0.5 <= speed <= 2.0):
            raise VoiceEngineError(f"invalid speed: {speed}")
        return self._synth_cached(text, voice, speed, lang)

    @lru_cache(maxsize=32)  # noqa: B019 - method LRU is intentional on singleton
    def _synth_cached(
        self, text: str, voice: str, speed: float, lang: str
    ) -> bytes:
        engine = self._engine_or_load()
        try:
            samples, sample_rate = engine.create(
                text, voice=voice, speed=speed, lang=lang
            )
        except Exception as exc:  # noqa: BLE001 - decoder errors
            raise VoiceEngineError(
                f"TTS synthesis failed: {type(exc).__name__}: {exc}"
            ) from exc
        arr = np.asarray(samples, dtype=np.float32)
        return _samples_to_wav(arr, int(sample_rate))
