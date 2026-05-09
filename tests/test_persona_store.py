"""Tests for the file-backed persona store."""

from __future__ import annotations

import numpy as np
import pytest

from x_agent.persona.schema import (
    PersonaSpec,
    TranscriptEntry,
    Voice,
    new_persona_id,
)
from x_agent.persona.store import PersonaNotFoundError, PersonaStore


@pytest.fixture()
def store(tmp_path):
    return PersonaStore(tmp_path / "personas")


def _make_spec(name: str = "Abhi") -> PersonaSpec:
    return PersonaSpec(
        id=new_persona_id(name),
        name=name,
        is_real_person=False,
        voice=Voice(formality=3, brevity="terse", humor="dry", sentence_length="short"),
        values=["clarity", "ship-it"],
    )


class TestPersonaStore:
    def test_save_and_load_round_trip(self, store: PersonaStore) -> None:
        spec = _make_spec()
        store.save(spec)
        loaded = store.load(spec.id)
        assert loaded.id == spec.id
        assert loaded.name == spec.name
        assert loaded.values == spec.values

    def test_load_unknown_raises(self, store: PersonaStore) -> None:
        with pytest.raises(PersonaNotFoundError):
            store.load("missing-12345678")

    def test_list_ids_returns_only_personas(self, store: PersonaStore, tmp_path) -> None:
        s1 = _make_spec("alpha")
        s2 = _make_spec("beta")
        store.save(s1)
        store.save(s2)
        # A stray sibling directory must be ignored.
        (store.base_dir / "not-a-persona").mkdir(exist_ok=True)
        ids = store.list_ids()
        assert s1.id in ids
        assert s2.id in ids
        assert "not-a-persona" not in ids

    def test_delete_removes_dir(self, store: PersonaStore) -> None:
        spec = _make_spec()
        store.save(spec)
        store.delete(spec.id)
        assert not store.exists(spec.id)
        with pytest.raises(PersonaNotFoundError):
            store.delete(spec.id)

    def test_transcript_append_and_read(self, store: PersonaStore) -> None:
        spec = _make_spec()
        store.save(spec)
        e1 = TranscriptEntry(dimension="humor", question="?", answer="dry")
        e2 = TranscriptEntry(dimension="values", question="?", answer="ship-it")
        store.append_transcript(spec.id, e1)
        store.append_transcript(spec.id, e2)
        out = store.read_transcript(spec.id)
        assert [e.dimension for e in out] == ["humor", "values"]

    def test_transcript_overwrite(self, store: PersonaStore) -> None:
        spec = _make_spec()
        store.save(spec)
        store.append_transcript(spec.id, TranscriptEntry(dimension="x", question="q", answer="a"))
        store.overwrite_transcript(
            spec.id,
            [TranscriptEntry(dimension="y", question="q2", answer="a2")],
        )
        out = store.read_transcript(spec.id)
        assert len(out) == 1 and out[0].dimension == "y"

    def test_embeddings_round_trip(self, store: PersonaStore) -> None:
        spec = _make_spec()
        store.save(spec)
        ids = ["a:1", "b:2", "c:3"]
        vectors = np.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            dtype=np.float32,
        )
        store.save_embeddings(spec.id, ids, vectors)
        result = store.load_embeddings(spec.id)
        assert result is not None
        loaded_ids, loaded_vec = result
        assert loaded_ids == ids
        assert loaded_vec.shape == (3, 3)
        assert np.allclose(loaded_vec, vectors)

    def test_embeddings_dim_mismatch_raises(self, store: PersonaStore) -> None:
        spec = _make_spec()
        store.save(spec)
        with pytest.raises(ValueError):
            store.save_embeddings(spec.id, ["a"], np.array([1.0]))  # 1D
        with pytest.raises(ValueError):
            store.save_embeddings(
                spec.id, ["a", "b"], np.array([[1.0]])  # length mismatch
            )

    def test_load_embeddings_missing_returns_none(self, store: PersonaStore) -> None:
        spec = _make_spec()
        store.save(spec)
        assert store.load_embeddings(spec.id) is None
