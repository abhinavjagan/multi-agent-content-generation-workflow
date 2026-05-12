"""faster-whisper speech-to-text engine wrapper.

The first call to :meth:`WhisperSTT.transcribe` triggers model download
(via ``huggingface_hub`` under the hood) to ``VOICE_MODEL_DIR``. After
that the engine is fully offline.

Upload safety:
- The audio bytes are written to a ``NamedTemporaryFile`` under
  ``/tmp`` (tmpfs in compose, capped at 64 MB), processed, then
  unlinked. We never preserve audio on disk and never log raw bytes.
- ``ffmpeg`` (installed in the runtime image) is used by faster-whisper
  to decode webm/ogg/mp4 to PCM. We pre-cap the input size in
  :mod:`x_agent.voice.security`.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..config import get_settings
from . import VoiceEngineError, VoiceEngineUnavailable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from faster_whisper import WhisperModel

log = logging.getLogger(__name__)


# ``faster-whisper`` accepts a directory via ``download_root`` and then
# uses HuggingFace under the hood. The model files live under
# ``<root>/models--Systran--faster-whisper-<size>``.
_VALID_MODELS = {
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "medium.en",
    "large-v2",
    "large-v3",
    "large-v3-turbo",
}


class WhisperSTT:
    """Process-wide singleton wrapping :class:`faster_whisper.WhisperModel`."""

    _instance: "Optional[WhisperSTT]" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._engine: "Optional[WhisperModel]" = None
        self._engine_lock = threading.Lock()

    @classmethod
    def get(cls) -> "WhisperSTT":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _validate_model_name(self, name: str) -> str:
        n = (name or "").strip()
        if n not in _VALID_MODELS:
            raise VoiceEngineUnavailable(
                f"invalid VOICE_STT_MODEL '{n}'; expected one of {sorted(_VALID_MODELS)}"
            )
        return n

    def _load(self) -> "WhisperModel":
        settings = get_settings()
        model_name = self._validate_model_name(settings.voice_stt_model)
        root = Path(settings.voice_model_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise VoiceEngineUnavailable(
                "faster-whisper is not installed; run `pip install faster-whisper`"
            ) from exc

        # Prefer a pre-staged local directory if it exists. faster-whisper
        # treats a path that satisfies ``os.path.isdir(...)`` as a local
        # model and skips the HuggingFace hub entirely -- which is the
        # only way to make this work behind corporate TLS interception
        # where Python's certifi bundle doesn't trust the MITM root.
        # ``scripts/download_models.sh`` populates exactly this layout
        # using host ``curl`` (which DOES trust the keychain).
        local_dir = root / f"faster-whisper-{model_name}"
        prestaged = (local_dir / "model.bin").is_file()
        if prestaged:
            target: str = str(local_dir)
            log.info(
                "voice.stt: loading from pre-staged local dir %s (compute=%s)",
                local_dir, settings.voice_stt_compute_type,
            )
        else:
            target = model_name
            log.info(
                "voice.stt: loading Whisper model %s (compute=%s) under %s; "
                "no pre-staged %s -> falling back to HuggingFace download",
                model_name, settings.voice_stt_compute_type, root, local_dir,
            )

        try:
            return WhisperModel(
                target,
                device="cpu",
                compute_type=settings.voice_stt_compute_type,
                download_root=str(root),
                # If we pre-staged, hard-fail rather than silently fall
                # through to the network (which would just hit the cert
                # issue again).
                local_files_only=prestaged,
            )
        except Exception as exc:  # noqa: BLE001
            raise VoiceEngineUnavailable(
                f"failed to load Whisper model: {type(exc).__name__}: {exc}"
            ) from exc

    def _engine_or_load(self) -> "WhisperModel":
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is None:
                self._engine = self._load()
        return self._engine

    def is_ready(self) -> bool:
        """Cheap probe: does the model cache directory contain any files?

        Does NOT trigger a download. Recognises two layouts:

        - **Pre-staged** (``scripts/download_models.sh``):
          ``<root>/faster-whisper-<name>/model.bin``. faster-whisper
          loads this dir directly via ``os.path.isdir`` -- no HF call.
        - **HuggingFace snapshot** (auto-download path):
          ``<root>/models--Systran--faster-whisper-<name>/snapshots/...``
          with a ``*.bin`` somewhere underneath. We don't validate
          beyond "directory exists, has a bin" because the snapshot
          hash varies across releases.
        """
        try:
            settings = get_settings()
            root = Path(settings.voice_model_dir).expanduser()
            if not root.is_dir():
                return False
            name = settings.voice_stt_model
            prestaged = root / f"faster-whisper-{name}" / "model.bin"
            if prestaged.is_file():
                return True
            hf_cache = root / f"models--Systran--faster-whisper-{name}"
            return hf_cache.is_dir() and any(hf_cache.rglob("*.bin"))
        except Exception:  # noqa: BLE001
            return False

    def warmup(self) -> None:
        """Best-effort eager init; logs and swallows errors."""
        try:
            self._engine_or_load()
        except VoiceEngineUnavailable as exc:
            log.warning("voice.stt: warmup skipped (%s)", exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("voice.stt: warmup failed: %s", exc)

    def transcribe(self, data: bytes, *, suffix: str) -> tuple[str, float]:
        """Transcribe ``data`` and return ``(text, duration_s)``.

        ``suffix`` is the file extension (``.webm``, ``.ogg``, ``.wav``,
        ``.mp4``, ``.m4a``) used for the temp file -- faster-whisper /
        ffmpeg sniffs by content not name, but giving a sane extension
        helps if the audio happens to be raw PCM.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise VoiceEngineError("transcribe() requires bytes")
        if not data:
            raise VoiceEngineError("empty audio payload")
        engine = self._engine_or_load()
        # Server-generated filename only; user-supplied filename is never
        # used to construct the on-disk path.
        with tempfile.NamedTemporaryFile(
            prefix="x_agent_stt_", suffix=suffix, dir="/tmp", delete=True
        ) as tmp:
            tmp.write(data)
            tmp.flush()
            try:
                segments, info = engine.transcribe(
                    tmp.name,
                    beam_size=5,
                    vad_filter=True,
                    # Cap inference work: we already enforced a duration cap
                    # upstream, but condition_on_previous_text=False also
                    # cuts a class of repetition loops on very long inputs.
                    condition_on_previous_text=False,
                )
                # ``segments`` is a generator -- consume eagerly so the
                # temp file can be unlinked the moment we exit the block.
                texts: list[str] = []
                for seg in segments:
                    if seg.text:
                        texts.append(seg.text)
                duration = float(getattr(info, "duration", 0.0) or 0.0)
            except Exception as exc:  # noqa: BLE001
                raise VoiceEngineError(
                    f"STT transcription failed: {type(exc).__name__}: {exc}"
                ) from exc
        text = "".join(texts).strip()
        return text, duration
