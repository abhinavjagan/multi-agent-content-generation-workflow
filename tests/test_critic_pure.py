"""Tests for pure-Python pieces of the persona subsystem.

We avoid hitting Ollama by either:
- patching ``ChatOllama`` to a stub that returns canned content, or
- exercising the JSON extractor + critic helpers directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from x_agent.nodes import _persona_block, _voice_signals_from_examples
from x_agent.persona import critic as critic_module
from x_agent.persona.critic import score_against_persona
from x_agent.persona.embedder import cosine_top_k
from x_agent.persona.interview import _coerce_enum, _coerce_int
from x_agent.persona.json_utils import JsonParseError, extract_json
from x_agent.persona.schema import PersonaSpec, Voice


# ----------------------------------------------------------------- json_utils


class TestExtractJson:
    def test_pure_object(self) -> None:
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_pure_array(self) -> None:
        assert extract_json("[1, 2, 3]") == [1, 2, 3]

    def test_fenced_json_block(self) -> None:
        text = 'Here you go:\n```json\n{"score": 4, "violations": []}\n```\nhope this helps'
        assert extract_json(text) == {"score": 4, "violations": []}

    def test_balanced_substring_with_preamble(self) -> None:
        text = 'Sure! {"score": 3, "suggestion": "tighten the prose"} thanks'
        assert extract_json(text) == {"score": 3, "suggestion": "tighten the prose"}

    def test_handles_nested_braces(self) -> None:
        text = 'noise {"a": {"b": 2}, "c": [1, 2]} tail'
        assert extract_json(text) == {"a": {"b": 2}, "c": [1, 2]}

    def test_handles_strings_with_braces(self) -> None:
        text = 'pre {"msg": "hello { world }"} post'
        assert extract_json(text) == {"msg": "hello { world }"}

    def test_empty_text_raises(self) -> None:
        with pytest.raises(JsonParseError):
            extract_json("")
        with pytest.raises(JsonParseError):
            extract_json("   ")

    def test_no_json_raises(self) -> None:
        with pytest.raises(JsonParseError):
            extract_json("just a sentence with no json")


# ----------------------------------------------------------------------- critic


def _spec() -> PersonaSpec:
    return PersonaSpec(
        id="abhi-12345678",
        name="Abhi",
        is_real_person=False,
        voice=Voice(formality=2, brevity="terse", humor="dry", sentence_length="short"),
        values=["clarity", "ship-it"],
        banned_phrases=["leverage", "unlock"],
    )


class _StubLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    def invoke(self, _messages):
        return SimpleNamespace(content=self._content)


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    monkeypatch.setattr(
        critic_module,
        "ChatOllama",
        lambda **_kw: _StubLLM(content),
    )


class TestScoreAgainstPersona:
    def test_clean_json_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, '{"score": 4, "violations": ["minor tone"], "suggestion": "tighten"}')
        out = score_against_persona(draft="hi", persona=_spec(), examples=[])
        assert out == {"score": 4, "violations": ["minor tone"], "suggestion": "tighten"}

    def test_score_clamped_to_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, '{"score": 99, "violations": []}')
        out = score_against_persona(draft="hi", persona=_spec(), examples=[])
        assert out["score"] == 5

    def test_negative_score_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, '{"score": -3, "violations": []}')
        out = score_against_persona(draft="hi", persona=_spec(), examples=[])
        assert out["score"] == 0

    def test_violations_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        many = ", ".join(f'"v{i}"' for i in range(20))
        _patch_llm(monkeypatch, f'{{"score": 2, "violations": [{many}]}}')
        out = score_against_persona(draft="hi", persona=_spec(), examples=[])
        assert len(out["violations"]) == 10

    def test_unparseable_output_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, "this is not json at all")
        out = score_against_persona(draft="hi", persona=_spec(), examples=[])
        # Fail-open: treat as a passing draft so the graph doesn't deadlock.
        assert out["score"] == 5
        assert out["violations"] == []

    def test_handles_fenced_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(
            monkeypatch,
            'preamble\n```json\n{"score": 3, "violations": ["banned: leverage"]}\n```\n',
        )
        out = score_against_persona(draft="we leverage tools", persona=_spec(), examples=[])
        assert out["score"] == 3
        assert out["violations"] == ["banned: leverage"]


# ----------------------------------------------------------------- embedder math


class TestCosineTopK:
    def test_orders_by_similarity(self) -> None:
        m = np.eye(3, dtype=np.float32)
        ids = ["a", "b", "c"]
        texts = ["A", "B", "C"]
        results = cosine_top_k(np.array([1.0, 0.5, 0.0]), m, ids, texts, k=3)
        assert results[0].chunk_id == "a"
        assert [r.chunk_id for r in results] == ["a", "b", "c"]

    def test_k_caps_results(self) -> None:
        m = np.eye(4, dtype=np.float32)
        ids = ["w", "x", "y", "z"]
        results = cosine_top_k(
            np.array([1.0, 0.0, 0.0, 0.0]), m, ids, ["w", "x", "y", "z"], k=2
        )
        assert len(results) == 2

    def test_empty_inputs(self) -> None:
        assert cosine_top_k(np.array([]), np.array([[]]), [], [], k=3) == []

    def test_dim_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            cosine_top_k(
                np.array([1.0, 0.0]),
                np.eye(3, dtype=np.float32),
                ["a", "b", "c"],
                ["a", "b", "c"],
                k=2,
            )


# ---------------------------------------------------- extractor coercion helpers


_HUMOR = ("dry", "warm", "sarcastic", "earnest", "none")


class TestCoerceEnum:
    def test_exact_match(self) -> None:
        assert _coerce_enum("dry", _HUMOR, "none") == "dry"

    def test_case_insensitive(self) -> None:
        assert _coerce_enum("WARM", _HUMOR, "none") == "warm"

    def test_pipe_joined_picks_first_match(self) -> None:
        # The original bug: model returned "sarcastic|earnest".
        assert _coerce_enum("sarcastic|earnest", _HUMOR, "none") == "sarcastic"

    def test_comma_joined_picks_first_match(self) -> None:
        assert _coerce_enum("warm, dry", _HUMOR, "none") == "warm"

    def test_unknown_falls_back_to_default(self) -> None:
        assert _coerce_enum("snarky", _HUMOR, "none") == "none"

    def test_none_returns_default(self) -> None:
        assert _coerce_enum(None, _HUMOR, "none") == "none"
        assert _coerce_enum("", _HUMOR, "none") == "none"


class TestCoerceInt:
    def test_within_range(self) -> None:
        assert _coerce_int(3, lo=1, hi=5, default=3) == 3

    def test_string_number(self) -> None:
        assert _coerce_int("4", lo=1, hi=5, default=3) == 4

    def test_float_string(self) -> None:
        assert _coerce_int("2.7", lo=1, hi=5, default=3) == 2

    def test_clamps_high(self) -> None:
        assert _coerce_int(99, lo=1, hi=5, default=3) == 5

    def test_clamps_low(self) -> None:
        assert _coerce_int(-2, lo=1, hi=5, default=3) == 1

    def test_garbage_uses_default(self) -> None:
        assert _coerce_int("very casual", lo=1, hi=5, default=3) == 3
        assert _coerce_int(None, lo=1, hi=5, default=2) == 2


# ----------------------------------------------------- voice signals heuristic


class TestVoiceSignals:
    def test_empty_examples(self) -> None:
        out = _voice_signals_from_examples([])
        assert out == {
            "mostly_lowercase": False,
            "drops_punctuation": False,
            "slang_used": [],
        }

    def test_detects_lowercase_slang(self) -> None:
        out = _voice_signals_from_examples([
            "yo this is broken lol lessgoo, wannna try this rn tbh",
            "lowkey idempotency is just dont charge me twice when i retry",
        ])
        assert out["mostly_lowercase"] is True
        slang = set(out["slang_used"])
        # Pulled actual tokens we'd want the writer to mirror.
        for token in {"lol", "lessgoo", "rn", "tbh", "lowkey"}:
            assert token in slang, f"missing {token} in {slang}"

    def test_uppercase_examples_not_lowercase_only(self) -> None:
        out = _voice_signals_from_examples([
            "Distributed tracing helps you locate slow spans in a request graph.",
            "Use OpenTelemetry semantic conventions for span names.",
        ])
        assert out["mostly_lowercase"] is False
        assert out["slang_used"] == []


class TestPersonaBlock:
    def test_no_persona_returns_empty(self) -> None:
        assert _persona_block(None, []) == ""

    def test_includes_mechanical_style_when_lowercase(self) -> None:
        spec = PersonaSpec(
            id="demo-12345678",
            name="Demo",
            voice=Voice(formality=2, brevity="terse", humor="dry", sentence_length="short"),
            banned_phrases=["leverage"],
        )
        block = _persona_block(spec.model_dump(mode="json"), [
            "yo lol lessgoo this is so cool rn tbh",
        ])
        assert "all lowercase" in block
        assert "lessgoo" in block or "lol" in block
        assert "leverage" in block
