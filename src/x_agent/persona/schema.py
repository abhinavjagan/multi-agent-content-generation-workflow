"""Pydantic schema for a captured persona.

A ``PersonaSpec`` is the structured distillation of an interview. It is the
single source of truth that drives the persona-conditioned writer prompt and
the consistency critic.

Files on disk store this as JSON; the writer composes a prompt from it; the
critic compares drafts back against it.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


HumorStyle = Literal["dry", "warm", "sarcastic", "earnest", "none"]
Brevity = Literal["terse", "balanced", "verbose"]
SentenceLength = Literal["short", "medium", "long"]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Return a filesystem-safe slug derived from ``name``."""
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug[:48] or "persona"


def new_persona_id(name: str) -> str:
    """Build a stable, collision-resistant persona id from ``name``."""
    return f"{_slugify(name)}-{uuid.uuid4().hex[:8]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Voice(BaseModel):
    """Quantized voice descriptors used in the writer prompt."""

    model_config = ConfigDict(extra="forbid")

    formality: int = Field(
        default=3,
        ge=1,
        le=5,
        description="1=street-casual, 5=academic.",
    )
    brevity: Brevity = "balanced"
    humor: HumorStyle = "none"
    sentence_length: SentenceLength = "medium"


class PersonaSpec(BaseModel):
    """Structured persona, derived from an interview transcript.

    The JSON spec lives next to a long-form ``personality.md`` profile on
    disk; the writer prompt and critic consume the markdown directly so
    they see a narrative description of the human rather than a chip
    cloud. The structured fields below are kept as machine-readable
    metadata (UI rendering, filtering, summaries) and are derived from
    the same extraction step that produces ``personality_md``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=120)

    # Consent + disclosure (governs auto-tagging on every generated post).
    is_real_person: bool = True
    consent_recorded_at: datetime | None = None
    disclosure_text: str = Field(
        default="",
        max_length=120,
        description=(
            "Short tag prepended/appended to every generated post when "
            "is_real_person is True. Required for real people."
        ),
    )

    voice: Voice = Field(default_factory=Voice)
    values: list[str] = Field(default_factory=list, max_length=20)
    opinions: list[str] = Field(default_factory=list, max_length=20)
    domains: list[str] = Field(default_factory=list, max_length=20)
    signature_phrases: list[str] = Field(default_factory=list, max_length=20)
    banned_phrases: list[str] = Field(default_factory=list, max_length=40)
    topics_loved: list[str] = Field(default_factory=list, max_length=20)
    topics_avoided: list[str] = Field(default_factory=list, max_length=20)

    decision_style: str = Field(default="", max_length=400)
    confidence_phrasing: str = Field(default="", max_length=400)

    # --- Richer personality dimensions (added v0.2.0) ---
    cadence: str = Field(
        default="",
        max_length=600,
        description=(
            "How sentences flow: rhythm, pauses, run-ons, comma habits, "
            "the way they 'sound' read aloud."
        ),
    )
    idioms: list[str] = Field(
        default_factory=list,
        max_length=30,
        description=(
            "Favorite words, quirks, weird grammar, openers and closers "
            "they actually use verbatim."
        ),
    )
    story_seeds: list[str] = Field(
        default_factory=list,
        max_length=12,
        description=(
            "Short prompts of anecdotes / examples this person reaches "
            "for over and over. Each item is one or two sentences."
        ),
    )
    pet_peeves: list[str] = Field(default_factory=list, max_length=20)
    enthusiasm_tells: list[str] = Field(
        default_factory=list,
        max_length=15,
        description=(
            "The phrases or punctuation moves that show up when they're "
            "actually excited."
        ),
    )
    conviction_signals: list[str] = Field(
        default_factory=list,
        max_length=15,
        description=(
            "How they signal certainty / strong belief (e.g. 'I will die "
            "on this hill', 'no notes', 'full stop')."
        ),
    )
    apology_pattern: str = Field(
        default="",
        max_length=600,
        description="The shape of an apology in their voice.",
    )
    emotional_range: str = Field(
        default="",
        max_length=600,
        description=(
            "How wide their emotional palette is in public, and what "
            "ranges they keep private."
        ),
    )

    # Long-form narrative profile (Markdown). This is what the writer
    # prompt reads. Stored alongside the JSON spec on disk so a user can
    # hand-edit it.
    personality_md: str = Field(
        default="",
        description=(
            "Markdown personality profile rendered from the spec + "
            "transcript. The writer prompt and critic consume this as "
            "the source of truth. Bounded loosely to keep prompts "
            "tractable but allowed to be long enough to capture nuance."
        ),
        max_length=40_000,
    )

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("id")
    @classmethod
    def _id_safe(cls, v: str) -> str:
        if not v or not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,79}", v):
            raise ValueError(
                "id must be lowercase alphanumeric with hyphens, 1-80 chars"
            )
        return v

    @field_validator(
        "values", "opinions", "domains", "signature_phrases",
        "banned_phrases", "topics_loved", "topics_avoided",
        "idioms", "story_seeds", "pet_peeves", "enthusiasm_tells",
        "conviction_signals",
    )
    @classmethod
    def _strip_and_dedupe(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in v:
            s = (item or "").strip()
            if not s or len(s) > 480:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out

    def requires_disclosure(self) -> bool:
        return self.is_real_person and bool(self.disclosure_text.strip())

    def ensure_disclosed(self, text: str) -> str:
        """If a disclosure is required, ensure ``text`` contains it (append if not)."""
        if not self.requires_disclosure():
            return text
        tag = self.disclosure_text.strip()
        if tag.lower() in text.lower():
            return text
        # Append on a new line so threading still works cleanly.
        return f"{text}\n\n{tag}".strip()


class TranscriptEntry(BaseModel):
    """One question/answer pair in the interview transcript."""

    model_config = ConfigDict(extra="forbid")

    dimension: str
    question: str
    answer: str
    is_followup: bool = False
    is_holdout: bool = False
    timestamp: datetime = Field(default_factory=utcnow)
