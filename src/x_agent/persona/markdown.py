"""Render a long-form personality.md profile from a PersonaSpec + transcript.

The markdown profile is the *primary* artifact the writer prompt and the
consistency critic consume. The JSON spec stays around as
machine-readable metadata, but the LLM sees this narrative -- with real
quotes pulled from the interview -- so it has receipts for every claim.

The renderer is deterministic and offline (no LLM calls). We pass it the
extracted ``PersonaSpec`` and the full transcript; it weaves them
together into a structured document the user can also hand-edit later.
"""

from __future__ import annotations

import re
from typing import Iterable

from .schema import PersonaSpec, TranscriptEntry


# Bound on the slice of personality.md that gets injected into the
# writer system prompt. We default to a generous 6 KB which leaves room
# for the topic, examples, web research, and the persona's own examples
# inside an 8K context window. Tunable per-call.
DEFAULT_PROMPT_BUDGET = 6_000


def _bullets(items: Iterable[str], *, max_items: int | None = None) -> str:
    items = [s.strip() for s in items if s and s.strip()]
    if max_items is not None:
        items = items[:max_items]
    if not items:
        return "_(none captured)_"
    return "\n".join(f"- {s}" for s in items)


def _para(text: str, *, fallback: str = "_(none captured)_") -> str:
    s = (text or "").strip()
    return s if s else fallback


def _short(text: str, limit: int = 320) -> str:
    """One-line shortened quote, suitable for inline quoting."""
    s = re.sub(r"\s+", " ", (text or "")).strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "\u2026"


def _quote_block(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    return "\n".join(f"> {line}" for line in s.splitlines() if line.strip())


def _transcript_by_dim(
    transcript: list[TranscriptEntry],
) -> dict[str, list[TranscriptEntry]]:
    by_dim: dict[str, list[TranscriptEntry]] = {}
    for entry in transcript:
        if entry.is_holdout:
            continue
        by_dim.setdefault(entry.dimension, []).append(entry)
    return by_dim


def _pick_quotes(
    transcript_by_dim: dict[str, list[TranscriptEntry]],
    dimensions: Iterable[str],
    *,
    max_quotes: int = 2,
    max_chars: int = 360,
) -> list[str]:
    """Pull up to ``max_quotes`` short quotes from the requested dimensions."""
    out: list[str] = []
    for dim in dimensions:
        for entry in transcript_by_dim.get(dim, []):
            answer = (entry.answer or "").strip()
            if not answer or answer == "(skipped)":
                continue
            out.append(_short(answer, max_chars))
            if len(out) >= max_quotes:
                return out
    return out


def render_personality_md(
    spec: PersonaSpec,
    transcript: list[TranscriptEntry] | None = None,
) -> str:
    """Produce the long-form markdown profile for ``spec``.

    The output is deterministic given the same inputs. Sections are
    pulled from both the structured ``PersonaSpec`` fields and verbatim
    transcript quotes so the writer prompt has both the distillation
    and the raw data.
    """
    transcript = transcript or []
    by_dim = _transcript_by_dim(transcript)

    voice = spec.voice
    lines: list[str] = []

    # --- Header ---
    lines.append(f"# {spec.name}")
    if spec.is_real_person:
        lines.append("")
        lines.append(f"_Real person._ Disclosure tag: `{spec.disclosure_text or '(none)'}`")
    else:
        lines.append("")
        lines.append("_Fictional persona._")
    lines.append("")

    # --- Overview / TL;DR ---
    lines.append("## TL;DR")
    lines.append("")
    tldr_parts = [
        f"Formality {voice.formality}/5",
        voice.brevity,
        voice.humor + " humor",
        voice.sentence_length + " sentences",
    ]
    if spec.cadence:
        tldr_parts.append(_short(spec.cadence, 160))
    lines.append("- " + " · ".join(tldr_parts))
    if spec.domains:
        lines.append("- Depth in: " + ", ".join(spec.domains[:6]))
    if spec.topics_loved:
        lines.append("- Pulls them to write: " + ", ".join(spec.topics_loved[:6]))
    if spec.topics_avoided:
        lines.append("- Won't write about: " + ", ".join(spec.topics_avoided[:6]))
    lines.append("")

    # --- Voice & cadence ---
    lines.append("## Voice & cadence")
    lines.append("")
    lines.append(
        f"- Formality: **{voice.formality}/5** "
        f"(1 = street-casual, 5 = academic)"
    )
    lines.append(f"- Brevity: **{voice.brevity}**")
    lines.append(f"- Humor: **{voice.humor}**")
    lines.append(f"- Typical sentence length: **{voice.sentence_length}**")
    if spec.cadence:
        lines.append("")
        lines.append(f"**Rhythm.** {spec.cadence}")
    for quote in _pick_quotes(by_dim, ["cadence", "style", "brevity"], max_quotes=2):
        lines.append("")
        lines.append(_quote_block(quote))
    lines.append("")

    # --- How they sound out loud ---
    lines.append("## How they sound out loud")
    lines.append("")
    if spec.idioms:
        lines.append("**Verbatim idioms, quirks, and openers/closers:**")
        lines.append("")
        lines.append(_bullets(spec.idioms, max_items=12))
        lines.append("")
    if spec.signature_phrases:
        lines.append("**Signature phrases (used sparingly, only when natural):**")
        lines.append("")
        lines.append(_bullets(spec.signature_phrases, max_items=10))
        lines.append("")
    for quote in _pick_quotes(by_dim, ["idioms", "signature_phrases", "style"], max_quotes=2):
        lines.append(_quote_block(quote))
        lines.append("")

    # --- Values ---
    lines.append("## What they care about")
    lines.append("")
    lines.append(_bullets(spec.values, max_items=10))
    for quote in _pick_quotes(by_dim, ["values"], max_quotes=1):
        lines.append("")
        lines.append(_quote_block(quote))
    lines.append("")

    # --- Opinions / conviction ---
    lines.append("## Strong opinions they'd defend")
    lines.append("")
    lines.append(_bullets(spec.opinions, max_items=10))
    if spec.conviction_signals:
        lines.append("")
        lines.append("**When they're planting a flag, the language sounds like:**")
        lines.append("")
        lines.append(_bullets(spec.conviction_signals, max_items=10))
    for quote in _pick_quotes(by_dim, ["opinions", "conviction_signals"], max_quotes=2):
        lines.append("")
        lines.append(_quote_block(quote))
    lines.append("")

    # --- Stories they reach for ---
    lines.append("## Stories they reach for")
    lines.append("")
    if spec.story_seeds:
        lines.append(_bullets(spec.story_seeds, max_items=8))
    else:
        lines.append("_(no anecdotes captured yet)_")
    for quote in _pick_quotes(by_dim, ["stories"], max_quotes=2):
        lines.append("")
        lines.append(_quote_block(quote))
    lines.append("")

    # --- Pet peeves + banned phrases ---
    lines.append("## What they refuse to sound like")
    lines.append("")
    if spec.banned_phrases:
        lines.append("**Banned phrases (NEVER use):**")
        lines.append("")
        lines.append(_bullets(spec.banned_phrases, max_items=20))
        lines.append("")
    if spec.pet_peeves:
        lines.append("**Pet peeves and patterns they wince at:**")
        lines.append("")
        lines.append(_bullets(spec.pet_peeves, max_items=12))
        lines.append("")
    if spec.topics_avoided:
        lines.append("**Topics off-limits:**")
        lines.append("")
        lines.append(_bullets(spec.topics_avoided, max_items=12))
        lines.append("")
    for quote in _pick_quotes(by_dim, ["pet_peeves", "boundaries", "banned_phrases"], max_quotes=2):
        lines.append(_quote_block(quote))
        lines.append("")

    # --- Decision style ---
    lines.append("## How they decide under uncertainty")
    lines.append("")
    lines.append(_para(spec.decision_style))
    if spec.confidence_phrasing:
        lines.append("")
        lines.append("**When they're not sure, they sound like:**")
        lines.append("")
        lines.append(_para(spec.confidence_phrasing))
    for quote in _pick_quotes(by_dim, ["decision_style", "confidence_phrasing"], max_quotes=2):
        lines.append("")
        lines.append(_quote_block(quote))
    lines.append("")

    # --- Emotional range + enthusiasm + apology ---
    lines.append("## Emotional range")
    lines.append("")
    lines.append(_para(spec.emotional_range))
    if spec.enthusiasm_tells:
        lines.append("")
        lines.append("**When they're actually excited, you'll see:**")
        lines.append("")
        lines.append(_bullets(spec.enthusiasm_tells, max_items=10))
    for quote in _pick_quotes(by_dim, ["enthusiasm_tells", "emotional_range"], max_quotes=2):
        lines.append("")
        lines.append(_quote_block(quote))
    lines.append("")

    lines.append("## How they apologize")
    lines.append("")
    lines.append(_para(spec.apology_pattern))
    for quote in _pick_quotes(by_dim, ["apology_pattern", "example_apology"], max_quotes=1):
        lines.append("")
        lines.append(_quote_block(quote))
    lines.append("")

    # --- Example posts in their voice ---
    lines.append("## Example posts in their voice")
    lines.append("")
    example_dims = [
        "example_explainer",
        "example_disagreement",
        "example_apology",
        "example_announce",
        "example_excited",
        "example_morning_pages",
    ]
    seen = 0
    for dim in example_dims:
        for entry in by_dim.get(dim, []):
            answer = (entry.answer or "").strip()
            if not answer or answer == "(skipped)":
                continue
            label = dim.replace("example_", "").replace("_", " ")
            lines.append(f"### {label}")
            lines.append("")
            lines.append(_quote_block(answer))
            lines.append("")
            seen += 1
            break  # one example per dim
    if seen == 0:
        lines.append("_(no writing samples captured yet)_")
        lines.append("")

    # --- Footer: how to use this ---
    lines.append("---")
    lines.append("")
    lines.append(
        "_This profile is the source of truth for the writer prompt and the "
        "persona-consistency critic. Hand-edit freely; the spec.json beside "
        "it carries the structured copy._"
    )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def summarize_for_prompt(
    personality_md: str,
    *,
    max_chars: int = DEFAULT_PROMPT_BUDGET,
) -> str:
    """Return a (possibly truncated) slice of ``personality_md`` for prompts.

    For tractable prompts we cap the markdown at ``max_chars`` characters.
    Truncation preserves whole sections where possible: we cut at the
    last ``\n## `` boundary before the limit, then add a short note so
    the LLM knows the document continues.
    """
    md = (personality_md or "").strip()
    if not md:
        return ""
    if len(md) <= max_chars:
        return md
    # Try to cut at the last section boundary.
    head = md[:max_chars]
    cut = head.rfind("\n## ")
    if cut > 200:
        truncated = head[:cut].rstrip()
    else:
        truncated = head.rstrip()
    return truncated + "\n\n_(profile continues; truncated for prompt budget)_\n"


__all__ = [
    "DEFAULT_PROMPT_BUDGET",
    "render_personality_md",
    "summarize_for_prompt",
]
