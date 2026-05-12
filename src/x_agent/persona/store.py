"""File-backed persona store.

Layout per persona::

    <persona_dir>/<persona_id>/
        spec.json          # PersonaSpec (structured metadata)
        personality.md     # long-form narrative profile (writer source of truth)
        transcript.jsonl   # one TranscriptEntry per line
        embeddings.npz     # optional: numpy archive {ids, vectors}

A ``filelock`` makes concurrent writes safe across processes (CLI + server).
The data is treated as sensitive PII: we use restrictive file permissions
on POSIX systems (0700 for the persona dir, 0600 for files).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np
from filelock import FileLock

from .schema import PersonaSpec, TranscriptEntry, utcnow

log = logging.getLogger(__name__)


class PersonaNotFoundError(KeyError):
    """Raised when a persona id is not present in the store."""


class PersonaWriteError(OSError):
    """Raised when the store cannot create / write the persona directory.

    The most common cause is the process running under a sandbox that
    blocks writes to ``persona_dir`` (e.g. macOS App Sandbox / Cursor's
    ``cursorsandbox`` helper which restricts writes outside the workspace).
    The message includes the offending path so the operator can either
    re-launch the server outside the sandbox or point ``X_AGENT_PERSONA_DIR``
    at a writable location.
    """


def _hint_for(path: Path) -> str:
    return (
        f"cannot write to persona directory {path!s}; the running process "
        "appears to be sandboxed. Either restart the server outside the sandbox "
        "or set X_AGENT_PERSONA_DIR to a writable path inside your workspace."
    )


class PersonaStore:
    """Filesystem-backed persona repository."""

    def __init__(self, base_dir: str | os.PathLike[str]) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        # Per-process memo of which persona dirs we've already mkdir'd +
        # chmod'd. Avoids repeated chmod attempts on every save (which is
        # noisy and, on sandboxed processes, raises EPERM every time).
        self._initialized_dirs: set[str] = set()
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # pragma: no cover - sandboxed envs
            raise PersonaWriteError(_hint_for(self.base_dir)) from exc
        # Restrict access on POSIX. Best-effort; not every FS supports chmod.
        try:
            os.chmod(self.base_dir, 0o700)
        except OSError:  # pragma: no cover - non-POSIX or read-only fs
            pass

    # ------------------------------------------------------------------ paths
    def _persona_dir(self, persona_id: str) -> Path:
        return self.base_dir / persona_id

    def _spec_path(self, persona_id: str) -> Path:
        return self._persona_dir(persona_id) / "spec.json"

    def _transcript_path(self, persona_id: str) -> Path:
        return self._persona_dir(persona_id) / "transcript.jsonl"

    def _embeddings_path(self, persona_id: str) -> Path:
        return self._persona_dir(persona_id) / "embeddings.npz"

    def _personality_path(self, persona_id: str) -> Path:
        return self._persona_dir(persona_id) / "personality.md"

    def _lock_path(self, persona_id: str) -> Path:
        return self._persona_dir(persona_id) / ".lock"

    # ------------------------------------------------------------------ utils
    def _ensure_dir(self, persona_id: str) -> Path:
        """Create the persona directory if it does not already exist.

        Raises :class:`PersonaWriteError` (an ``OSError`` subclass) with a
        human-friendly hint when the process cannot create the directory
        (e.g. sandboxed). ``chmod`` is attempted only the first time we
        touch a given directory in this process, then memoised, so that
        subsequent saves don't re-trigger noisy permission errors on
        platforms where chmod is restricted.
        """
        d = self._persona_dir(persona_id)
        if persona_id in self._initialized_dirs and d.is_dir():
            return d
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PersonaWriteError(_hint_for(d)) from exc
        try:
            os.chmod(d, 0o700)
        except OSError:  # pragma: no cover - non-POSIX or sandboxed fs
            pass
        self._initialized_dirs.add(persona_id)
        return d

    @contextmanager
    def _lock(self, persona_id: str) -> Iterator[None]:
        self._ensure_dir(persona_id)
        lock = FileLock(str(self._lock_path(persona_id)), timeout=10)
        with lock:
            yield

    @staticmethod
    def _restrict(path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover
            pass

    # ------------------------------------------------------------------ specs
    def exists(self, persona_id: str) -> bool:
        return self._spec_path(persona_id).is_file()

    def list_ids(self) -> list[str]:
        if not self.base_dir.is_dir():
            return []
        out: list[str] = []
        for p in sorted(self.base_dir.iterdir()):
            if p.is_dir() and (p / "spec.json").is_file():
                out.append(p.name)
        return out

    def load(self, persona_id: str) -> PersonaSpec:
        path = self._spec_path(persona_id)
        if not path.is_file():
            raise PersonaNotFoundError(persona_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        return PersonaSpec.model_validate(data)

    def save(self, spec: PersonaSpec) -> None:
        with self._lock(spec.id):
            spec.updated_at = utcnow()
            path = self._spec_path(spec.id)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(path)
            self._restrict(path)
            # Mirror the long-form personality.md alongside the spec when
            # the field is populated. This keeps the editable artifact in
            # sync with the structured metadata.
            md = (spec.personality_md or "").strip()
            if md:
                md_path = self._personality_path(spec.id)
                tmp_md = md_path.with_suffix(".md.tmp")
                tmp_md.write_text(md + "\n", encoding="utf-8")
                tmp_md.replace(md_path)
                self._restrict(md_path)
            log.info("persona.save id=%s", spec.id)

    # -------------------------------------------------------- personality.md
    def read_personality(self, persona_id: str) -> str:
        """Return the markdown personality profile for ``persona_id``.

        Falls back to ``spec.personality_md`` if the file is missing
        (older personas predate the on-disk artifact).
        """
        path = self._personality_path(persona_id)
        if path.is_file():
            return path.read_text(encoding="utf-8")
        if not self.exists(persona_id):
            raise PersonaNotFoundError(persona_id)
        spec = self.load(persona_id)
        return spec.personality_md or ""

    def write_personality(self, persona_id: str, markdown: str) -> None:
        """Overwrite the personality.md profile and mirror it into the spec.

        The writer prompt reads ``personality.md`` directly so this is
        the user's escape hatch for hand-tuning their persona's voice.
        We also update ``spec.personality_md`` so the JSON stays
        consistent.
        """
        if not self.exists(persona_id):
            raise PersonaNotFoundError(persona_id)
        md = (markdown or "").strip()
        with self._lock(persona_id):
            md_path = self._personality_path(persona_id)
            tmp = md_path.with_suffix(".md.tmp")
            tmp.write_text(md + ("\n" if md else ""), encoding="utf-8")
            tmp.replace(md_path)
            self._restrict(md_path)
            spec = self.load(persona_id)
            spec.personality_md = md
            spec.updated_at = utcnow()
            spec_path = self._spec_path(persona_id)
            tmp_spec = spec_path.with_suffix(".json.tmp")
            tmp_spec.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
            tmp_spec.replace(spec_path)
            self._restrict(spec_path)
            log.info("persona.write_personality id=%s len=%d", persona_id, len(md))

    def delete(self, persona_id: str) -> None:
        if not self.exists(persona_id):
            raise PersonaNotFoundError(persona_id)
        shutil.rmtree(self._persona_dir(persona_id))
        log.info("persona.delete id=%s", persona_id)

    # -------------------------------------------------------------- transcript
    def append_transcript(self, persona_id: str, entry: TranscriptEntry) -> None:
        with self._lock(persona_id):
            path = self._transcript_path(persona_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(entry.model_dump_json() + "\n")
            self._restrict(path)

    def read_transcript(self, persona_id: str) -> list[TranscriptEntry]:
        path = self._transcript_path(persona_id)
        if not path.is_file():
            return []
        out: list[TranscriptEntry] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(TranscriptEntry.model_validate_json(line))
        return out

    def overwrite_transcript(
        self, persona_id: str, entries: list[TranscriptEntry]
    ) -> None:
        with self._lock(persona_id):
            path = self._transcript_path(persona_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(entry.model_dump_json() + "\n")
            tmp.replace(path)
            self._restrict(path)

    # --------------------------------------------------------------- vectors
    def save_embeddings(
        self, persona_id: str, ids: list[str], vectors: np.ndarray
    ) -> None:
        if vectors.ndim != 2:
            raise ValueError("vectors must be a 2D array (n_chunks, dim)")
        if len(ids) != vectors.shape[0]:
            raise ValueError("ids and vectors length mismatch")
        with self._lock(persona_id):
            path = self._embeddings_path(persona_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            # ``np.savez`` appends ``.npz`` to bare filenames, but accepts a
            # file-like object verbatim. Write to a tmp handle then atomic-rename.
            tmp = path.with_name(path.name + ".tmp")
            with tmp.open("wb") as f:
                np.savez(
                    f,
                    ids=np.array(ids, dtype=object),
                    vectors=vectors.astype(np.float32),
                )
            tmp.replace(path)
            self._restrict(path)

    def load_embeddings(self, persona_id: str) -> tuple[list[str], np.ndarray] | None:
        path = self._embeddings_path(persona_id)
        if not path.is_file():
            return None
        with np.load(path, allow_pickle=True) as data:
            ids = list(data["ids"].tolist())
            vectors = np.asarray(data["vectors"], dtype=np.float32)
        return ids, vectors


_default_store: PersonaStore | None = None


def get_default_store() -> PersonaStore:
    """Return the process-wide default store, lazily constructed."""
    global _default_store
    if _default_store is None:
        from ..config import get_settings
        _default_store = PersonaStore(get_settings().persona_dir)
    return _default_store


def reset_default_store() -> None:
    """Clear the cached default store (useful for tests)."""
    global _default_store
    _default_store = None
