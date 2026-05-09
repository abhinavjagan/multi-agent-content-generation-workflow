"""Tiny in-process embedding store backed by Ollama embeddings.

We keep retrieval simple: pull every chunk into memory at load time and run a
numpy cosine top-k. Interview transcripts are small (tens of KB), so a vector
DB would be overkill.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .schema import TranscriptEntry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float


def _format_chunk(entry: TranscriptEntry) -> tuple[str, str]:
    """Return ``(chunk_id, text)`` for a transcript entry."""
    chunk_id = f"{entry.dimension}:{int(entry.timestamp.timestamp())}"
    text = f"Q: {entry.question}\nA: {entry.answer}"
    return chunk_id, text


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return matrix / norms


def cosine_top_k(
    query_vec: np.ndarray,
    matrix: np.ndarray,
    ids: Sequence[str],
    texts: Sequence[str],
    k: int,
) -> list[RetrievedChunk]:
    """Return up to ``k`` highest-cosine matches against ``matrix``."""
    if matrix.size == 0 or query_vec.size == 0:
        return []
    if matrix.shape[1] != query_vec.shape[0]:
        raise ValueError(
            f"dim mismatch: query={query_vec.shape[0]} matrix={matrix.shape[1]}"
        )
    q = query_vec / max(float(np.linalg.norm(query_vec)), 1e-12)
    m = _normalize(matrix.astype(np.float32))
    scores = m @ q.astype(np.float32)
    top_idx = np.argsort(-scores)[: min(k, len(ids))]
    return [
        RetrievedChunk(chunk_id=ids[i], text=texts[i], score=float(scores[i]))
        for i in top_idx
    ]


class PersonaEmbedder:
    """Wrapper over ``OllamaEmbeddings`` plus utilities to build / use an index.

    The wrapper lazily constructs the embedding client so unit tests can
    inject a fake ``embed_documents`` / ``embed_query`` callable.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        client: object | None = None,
    ) -> None:
        from ..config import get_settings

        s = get_settings()
        self._base_url = base_url or s.ollama_base_url
        self._model = model or s.embedding_model
        self._client = client

    @property
    def client(self) -> object:
        if self._client is not None:
            return self._client
        from langchain_ollama import OllamaEmbeddings  # imported lazily

        self._client = OllamaEmbeddings(
            base_url=self._base_url,
            model=self._model,
        )
        return self._client

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vectors = self.client.embed_documents(texts)  # type: ignore[attr-defined]
        return np.asarray(vectors, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        vec = self.client.embed_query(text)  # type: ignore[attr-defined]
        return np.asarray(vec, dtype=np.float32)

    def build_index(
        self, transcript: list[TranscriptEntry]
    ) -> tuple[list[str], list[str], np.ndarray]:
        """Embed every transcript entry; return ``(ids, texts, vectors)``."""
        if not transcript:
            return [], [], np.zeros((0, 0), dtype=np.float32)
        ids: list[str] = []
        texts: list[str] = []
        for entry in transcript:
            cid, text = _format_chunk(entry)
            ids.append(cid)
            texts.append(text)
        vectors = self.embed_texts(texts)
        return ids, texts, vectors

    def retrieve(
        self,
        query: str,
        ids: Sequence[str],
        texts: Sequence[str],
        vectors: np.ndarray,
        k: int = 4,
    ) -> list[RetrievedChunk]:
        if not ids:
            return []
        q = self.embed_query(query)
        return cosine_top_k(q, vectors, ids, texts, k)
