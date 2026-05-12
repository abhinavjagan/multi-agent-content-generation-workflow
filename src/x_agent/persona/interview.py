"""Interview helpers: judge, extractor, and small utilities used by the graph."""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from ..config import get_settings
from .json_utils import JsonParseError, extract_json
from .markdown import render_personality_md
from .questions import Question
from .schema import (
    PersonaSpec,
    TranscriptEntry,
    Voice,
    new_persona_id,
    utcnow,
)

log = logging.getLogger(__name__)


_JUDGE_SYSTEM = (
    "You judge whether an interview answer is rich enough to capture a person's "
    "trait. Reply ONLY with strict JSON: "
    '{"sufficient": true|false, "followup": "<question>"|null, "reason": "<short>"}\n'
    "Be strict but reasonable. Sufficient means: concrete, multi-sentence, with "
    "examples or specifics; not a one-word or vague answer."
)


_EXTRACT_SYSTEM = (
    "You convert an interview transcript into a structured JSON persona. "
    "Output STRICT JSON only - no preamble, no markdown, no code fences. "
    "Pick EXACTLY ONE option for every enum field (never 'a|b' or 'a, b').\n"
    "\n"
    "Schema:\n"
    "{\n"
    '  "voice": {"formality": 1-5, "brevity": "terse|balanced|verbose", '
    '"humor": "dry|warm|sarcastic|earnest|none", '
    '"sentence_length": "short|medium|long"},\n'
    '  "values": [str], "opinions": [str], "domains": [str],\n'
    '  "signature_phrases": [str], "banned_phrases": [str],\n'
    '  "topics_loved": [str], "topics_avoided": [str],\n'
    '  "decision_style": str, "confidence_phrasing": str,\n'
    '  "cadence": str,\n'
    '  "idioms": [str], "story_seeds": [str], "pet_peeves": [str],\n'
    '  "enthusiasm_tells": [str], "conviction_signals": [str],\n'
    '  "apology_pattern": str, "emotional_range": str\n'
    "}\n"
    "\n"
    "Calibration:\n"
    "- formality: 1 if they write lowercase / use slang like 'rn, tbh, ngl, "
    "lol, lessgoo'. 2 casual but professional. 3 neutral. 4 formal. 5 academic.\n"
    "- signature_phrases & idioms: ACTUAL recurring tokens / openers / "
    "catchphrases the subject literally used (e.g. 'lessgoo', 'lowkey', "
    "'tbh', 'lol', 'rn', 'imo', 'ngl', 'nahh'). Pull the slang verbatim. "
    "Do NOT summarize, do NOT echo follow-up boilerplate. 'idioms' is the "
    "broader bag: weird grammar, intentional misspellings, in-group slang, "
    "openers/closers; 'signature_phrases' is the short list they'd be "
    "recognized for.\n"
    "- cadence: ONE-SENTENCE description of their rhythm (e.g. 'short "
    "staccato beats with frequent dashes', 'long winding sentences held "
    "together by commas').\n"
    "- story_seeds: short prompts (one-two sentences each) of anecdotes "
    "they reach for. Quote the gist, not the full text.\n"
    "- enthusiasm_tells: the actual moves (caps, exclamation runs, words "
    "like 'omg', 'lessgoo') that show they care.\n"
    "- conviction_signals: phrases that plant a flag ('full stop', 'no "
    "notes', 'I will die on this hill').\n"
    "- pet_peeves: behaviors they wince at (vague-posting, reply guys, "
    "screenshots-as-content). Short, concrete items.\n"
    "- apology_pattern: ONE-SENTENCE description of how their apology "
    "is structured.\n"
    "- emotional_range: ONE-SENTENCE description of the range they show "
    "in public vs. keep private.\n"
    "- banned_phrases: items the subject said they hate or refuse to use.\n"
    "- values / opinions: short noun phrases, not paragraphs.\n"
    "\n"
    "Only include phrases grounded in the transcript. Lists at most 12 items. "
    "Strings under 480 chars. If a field has no signal, return [] or \"\" but "
    "keep the key."
)


def _llm(model: str | None = None, *, temperature: float = 0.0) -> ChatOllama:
    s = get_settings()
    return ChatOllama(
        base_url=s.ollama_base_url,
        model=model or s.ollama_model,
        temperature=temperature,
    )


_HUMOR_ALLOWED = ("dry", "warm", "sarcastic", "earnest", "none")
_BREVITY_ALLOWED = ("terse", "balanced", "verbose")
_SENTENCE_ALLOWED = ("short", "medium", "long")


def _coerce_enum(value: Any, allowed: tuple[str, ...], default: str) -> str:
    """Coerce LLM output to a single allowed enum value.

    Accepts any of:
      - exact match,
      - "a|b" or "a, b" -> takes the first matching option,
      - case-insensitive match.
    Falls back to ``default`` rather than raising; persona extraction must
    remain robust to LLM drift across local models.
    """
    if not value:
        return default
    raw = str(value).strip().lower()
    # Direct hit.
    if raw in allowed:
        return raw
    # Multi-value strings ("dry|sarcastic", "warm, earnest") -> first match.
    for candidate in re.split(r"[|,/;\s]+", raw):
        cand = candidate.strip()
        if cand in allowed:
            return cand
    return default


def _coerce_int(value: Any, *, lo: int, hi: int, default: int) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def judge_answer_completeness(
    *,
    question: Question,
    answer: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Return ``{"sufficient": bool, "followup": str|None, "reason": str}``.

    Generative answers are always sufficient (the answer IS the artifact we
    wanted). One-shot LLM call with strict JSON output; on parse failure we
    treat the answer as sufficient (fail open: don't trap the user in loops).
    """
    if question.kind == "generative":
        return {"sufficient": True, "followup": None, "reason": "generative_probe"}
    if not answer or len(answer.strip()) < 30:
        return {
            "sufficient": False,
            "followup": (
                "Could you expand on that with a concrete example or two?"
            ),
            "reason": "too_short",
        }
    user = (
        f"Dimension: {question.dimension}\n"
        f"Question: {question.prompt}\n"
        f"Answer: {answer.strip()}\n"
    )
    try:
        resp = _llm(model).invoke(
            [SystemMessage(content=_JUDGE_SYSTEM), HumanMessage(content=user)]
        )
        data = extract_json(resp.content or "")
        return {
            "sufficient": bool(data.get("sufficient", True)),
            "followup": (data.get("followup") or None),
            "reason": str(data.get("reason", ""))[:200],
        }
    except (JsonParseError, Exception) as exc:  # noqa: BLE001 - fail open
        log.warning("judge parse failed, accepting answer: %s", exc)
        return {"sufficient": True, "followup": None, "reason": "judge_error"}


def extract_persona_spec(
    *,
    name: str,
    is_real_person: bool,
    disclosure_text: str,
    transcript: list[TranscriptEntry],
    persona_id: str | None = None,
    model: str | None = None,
) -> PersonaSpec:
    """Run an LLM extraction over the transcript and assemble a PersonaSpec."""
    if not transcript:
        raise ValueError("cannot extract persona from empty transcript")

    user_lines = [f"Subject name: {name}", "Transcript:"]
    for entry in transcript:
        if entry.is_holdout:
            continue
        prefix = "[follow-up] " if entry.is_followup else ""
        user_lines.append(
            f"- ({entry.dimension}) {prefix}Q: {entry.question}\n  A: {entry.answer}"
        )
    user_msg = "\n".join(user_lines)

    resp = _llm(model, temperature=0.0).invoke(
        [SystemMessage(content=_EXTRACT_SYSTEM), HumanMessage(content=user_msg)]
    )
    try:
        data = extract_json(resp.content or "")
    except JsonParseError as exc:
        raise RuntimeError(f"persona extraction failed to produce JSON: {exc}") from exc

    voice_data = data.get("voice") or {}
    spec = PersonaSpec(
        id=persona_id or new_persona_id(name),
        name=name,
        is_real_person=is_real_person,
        consent_recorded_at=utcnow() if is_real_person else None,
        disclosure_text=disclosure_text,
        voice=Voice(
            formality=_coerce_int(
                voice_data.get("formality"), lo=1, hi=5, default=3
            ),
            brevity=_coerce_enum(
                voice_data.get("brevity"), _BREVITY_ALLOWED, "balanced"
            ),
            humor=_coerce_enum(
                voice_data.get("humor"), _HUMOR_ALLOWED, "none"
            ),
            sentence_length=_coerce_enum(
                voice_data.get("sentence_length"),
                _SENTENCE_ALLOWED,
                "medium",
            ),
        ),
        values=list(data.get("values") or []),
        opinions=list(data.get("opinions") or []),
        domains=list(data.get("domains") or []),
        signature_phrases=list(data.get("signature_phrases") or []),
        banned_phrases=list(data.get("banned_phrases") or []),
        topics_loved=list(data.get("topics_loved") or []),
        topics_avoided=list(data.get("topics_avoided") or []),
        decision_style=str(data.get("decision_style") or ""),
        confidence_phrasing=str(data.get("confidence_phrasing") or ""),
        cadence=str(data.get("cadence") or ""),
        idioms=list(data.get("idioms") or []),
        story_seeds=list(data.get("story_seeds") or []),
        pet_peeves=list(data.get("pet_peeves") or []),
        enthusiasm_tells=list(data.get("enthusiasm_tells") or []),
        conviction_signals=list(data.get("conviction_signals") or []),
        apology_pattern=str(data.get("apology_pattern") or ""),
        emotional_range=str(data.get("emotional_range") or ""),
    )
    # Render the long-form personality.md narrative deterministically from
    # the spec + transcript. This is the artifact the writer prompt and
    # critic actually consume; the JSON spec stays around as metadata for
    # the UI and as an editable summary.
    spec.personality_md = render_personality_md(spec, transcript)
    return spec
