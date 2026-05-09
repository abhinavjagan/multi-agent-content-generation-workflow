"""Persona consistency critic.

Given a draft, the active ``PersonaSpec``, and a few retrieved examples of
how the person actually writes, score the draft 0-5 and list violations.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from ..config import get_settings
from .json_utils import JsonParseError, extract_json
from .schema import PersonaSpec

log = logging.getLogger(__name__)


_CRITIC_SYSTEM = (
    "You are a strict consistency judge for an AI persona writer.\n"
    "Given a PERSONA spec, retrieved EXAMPLES of how the person actually writes, "
    "and a DRAFT, score the draft 0-5 on whether it sounds like that person.\n"
    "5 = indistinguishable from their voice. 0 = completely off.\n"
    "Deduct points for: tone or formality mismatch, banned phrases used, "
    "signature style absent, value violations, generic LLM filler "
    "('In a world where', 'unlock', 'leverage', 'game-changer', etc.), "
    "wrong sentence-length cadence.\n"
    "Reply with STRICT JSON only:\n"
    '{"score": 0-5, "violations": [str, ...], "suggestion": "<one sentence>"}'
)


def _summarize_persona(p: PersonaSpec) -> str:
    parts = [
        f"Name: {p.name}",
        (
            f"Voice: formality={p.voice.formality}/5, brevity={p.voice.brevity}, "
            f"humor={p.voice.humor}, sentences={p.voice.sentence_length}"
        ),
    ]
    if p.values:
        parts.append("Values: " + "; ".join(p.values[:6]))
    if p.opinions:
        parts.append("Opinions: " + "; ".join(p.opinions[:6]))
    if p.signature_phrases:
        parts.append("Signature phrases: " + ", ".join(p.signature_phrases[:8]))
    if p.banned_phrases:
        parts.append("Banned phrases: " + ", ".join(p.banned_phrases[:12]))
    if p.topics_avoided:
        parts.append("Avoids: " + ", ".join(p.topics_avoided[:6]))
    if p.confidence_phrasing:
        parts.append(f"Confidence phrasing: {p.confidence_phrasing}")
    if p.decision_style:
        parts.append(f"Decision style: {p.decision_style}")
    return "\n".join(parts)


def score_against_persona(
    *,
    draft: str,
    persona: PersonaSpec,
    examples: list[str],
    model: str | None = None,
) -> dict[str, Any]:
    """Return ``{"score": int, "violations": list[str], "suggestion": str}``.

    Falls back to a permissive score on any LLM/JSON failure so we never
    deadlock the graph.
    """
    s = get_settings()
    chosen = model or s.critic_model or s.ollama_model

    examples_block = "\n\n".join(
        f"EXAMPLE {i + 1}:\n{ex}" for i, ex in enumerate(examples[:6])
    ) or "(no examples available)"

    user = (
        f"PERSONA:\n{_summarize_persona(persona)}\n\n"
        f"{examples_block}\n\n"
        f"DRAFT:\n{draft.strip()}\n"
    )
    llm = ChatOllama(
        base_url=s.ollama_base_url, model=chosen, temperature=0.0
    )
    try:
        resp = llm.invoke(
            [SystemMessage(content=_CRITIC_SYSTEM), HumanMessage(content=user)]
        )
        data = extract_json(resp.content or "")
        raw_score = data.get("score", 5)
        try:
            score = int(round(float(raw_score)))
        except (TypeError, ValueError):
            score = 5
        score = max(0, min(5, score))
        violations = [
            str(v).strip()[:200]
            for v in (data.get("violations") or [])
            if str(v).strip()
        ][:10]
        suggestion = str(data.get("suggestion") or "").strip()[:400]
        return {"score": score, "violations": violations, "suggestion": suggestion}
    except (JsonParseError, Exception) as exc:  # noqa: BLE001 - fail open
        log.warning("critic parse failed; passing draft through: %s", exc)
        return {"score": 5, "violations": [], "suggestion": ""}
