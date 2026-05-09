"""Tests for the persona schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from x_agent.persona.schema import (
    PersonaSpec,
    TranscriptEntry,
    Voice,
    new_persona_id,
    utcnow,
)


def _spec(**overrides):
    base = dict(
        id="abhi-12345678",
        name="Abhi",
        is_real_person=False,
        voice=Voice(formality=3, brevity="terse", humor="dry", sentence_length="short"),
    )
    base.update(overrides)
    return PersonaSpec(**base)


class TestVoice:
    def test_defaults(self) -> None:
        v = Voice()
        assert v.formality == 3
        assert v.brevity == "balanced"
        assert v.humor == "none"

    def test_formality_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Voice(formality=0)
        with pytest.raises(ValidationError):
            Voice(formality=6)

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            Voice(weirdness=5)  # type: ignore[call-arg]


class TestPersonaSpec:
    def test_id_format_enforced(self) -> None:
        with pytest.raises(ValidationError):
            _spec(id="UPPER_CASE")
        with pytest.raises(ValidationError):
            _spec(id="bad space")
        with pytest.raises(ValidationError):
            _spec(id="")

    def test_round_trip_json(self) -> None:
        s = _spec(values=["clarity", "ship-it", "respect"], banned_phrases=["leverage"])
        data = s.model_dump_json()
        s2 = PersonaSpec.model_validate_json(data)
        assert s2 == s

    def test_lists_dedupe_and_strip(self) -> None:
        s = _spec(banned_phrases=["leverage", "  leverage  ", "Leverage", "unlock", ""])
        assert s.banned_phrases == ["leverage", "unlock"]

    def test_lists_drop_oversized_items(self) -> None:
        oversized = "x" * 500
        s = _spec(values=[oversized, "ok"])
        assert s.values == ["ok"]

    def test_disclosure_required_only_for_real_with_text(self) -> None:
        synthetic = _spec(is_real_person=False, disclosure_text="ignored")
        assert not synthetic.requires_disclosure()
        real_no_text = _spec(is_real_person=True, disclosure_text="")
        assert not real_no_text.requires_disclosure()
        real_with_text = _spec(is_real_person=True, disclosure_text="[AI persona]")
        assert real_with_text.requires_disclosure()

    def test_ensure_disclosed_adds_when_missing(self) -> None:
        spec = _spec(is_real_person=True, disclosure_text="[AI persona of @abhi]")
        out = spec.ensure_disclosed("hello world")
        assert out.endswith("[AI persona of @abhi]")

    def test_ensure_disclosed_idempotent(self) -> None:
        spec = _spec(is_real_person=True, disclosure_text="[AI persona]")
        already = "hello [AI persona] world"
        assert spec.ensure_disclosed(already) == already

    def test_ensure_disclosed_noop_for_synthetic(self) -> None:
        spec = _spec(is_real_person=False, disclosure_text="[AI persona]")
        assert spec.ensure_disclosed("text") == "text"


class TestNewPersonaId:
    def test_slug_format(self) -> None:
        pid = new_persona_id("Abhi Kumar")
        assert pid.startswith("abhi-kumar-")
        assert len(pid.split("-")[-1]) == 8

    def test_handles_punctuation(self) -> None:
        pid = new_persona_id("!!! @@@ ###")
        assert pid.startswith("persona-")


class TestTranscriptEntry:
    def test_round_trip(self) -> None:
        entry = TranscriptEntry(
            dimension="humor",
            question="What's funny?",
            answer="Nothing.",
            timestamp=utcnow(),
        )
        data = entry.model_dump_json()
        round_tripped = TranscriptEntry.model_validate_json(data)
        assert round_tripped.dimension == "humor"
