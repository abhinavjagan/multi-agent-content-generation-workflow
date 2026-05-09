"""Interview question bank.

Each ``Question`` belongs to a trait ``dimension``. The interview graph walks
the dimensions in order, asks the question, optionally probes for a follow-up
based on an LLM judge, then advances. ``kind`` distinguishes free-form prose
from generative probes (where we ask the subject to write a sample post).

Edit this list freely; the rest of the system is data-driven.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

QuestionKind = Literal["open", "generative"]


@dataclass(frozen=True)
class Question:
    dimension: str
    prompt: str
    kind: QuestionKind = "open"
    required: bool = True
    is_holdout: bool = False


QUESTION_BANK: list[Question] = [
    # Style + voice
    Question(
        dimension="style",
        prompt=(
            "How would you describe the way you write to friends versus colleagues? "
            "Give a concrete example of a sentence you'd say in each setting."
        ),
    ),
    Question(
        dimension="brevity",
        prompt=(
            "Are you more of a 'one short sentence is enough' person, or do you "
            "like to give context? Why?"
        ),
    ),
    Question(
        dimension="humor",
        prompt=(
            "Describe your humor in one or two sentences. When does it show up "
            "and when does it not?"
        ),
    ),
    Question(
        dimension="humor",
        prompt="Tell me a small joke or one-liner you'd actually use.",
        is_holdout=True,
    ),
    # Values + opinions
    Question(
        dimension="values",
        prompt=(
            "List three things you genuinely care about in your work or life. "
            "For each, one sentence on why."
        ),
    ),
    Question(
        dimension="opinions",
        prompt=(
            "Give two 'X over Y' opinions you'd defend in public. For each, "
            "one sentence on why."
        ),
    ),
    # Boundaries
    Question(
        dimension="boundaries",
        prompt=(
            "What kinds of things would you NEVER post or say in public? "
            "Be specific - topics, tones, phrases."
        ),
    ),
    Question(
        dimension="banned_phrases",
        prompt=(
            "Which words or phrases do you find cringey or hate seeing in posts? "
            "(e.g. 'leverage', 'unlock', 'game-changer'). List a handful."
        ),
    ),
    Question(
        dimension="signature_phrases",
        prompt=(
            "Are there phrases or constructions you use a lot that feel like "
            "yours? List a few if so."
        ),
    ),
    # Domain + topics
    Question(
        dimension="domains",
        prompt=(
            "What topics do you have real depth in? Where do you have opinions "
            "people listen to?"
        ),
    ),
    Question(
        dimension="topics_loved",
        prompt="What topics make you want to write a post immediately?",
    ),
    Question(
        dimension="topics_avoided",
        prompt="What topics do you actively avoid posting about, and why?",
    ),
    # Decision + uncertainty
    Question(
        dimension="decision_style",
        prompt=(
            "When you have to make a hard call with incomplete info, walk me "
            "through how you decide. Bullet points are fine."
        ),
    ),
    Question(
        dimension="confidence_phrasing",
        prompt=(
            "How do you usually signal that you're uncertain about something? "
            "Give the kinds of phrases you'd actually use."
        ),
    ),
    # Generative probes - these become high-quality few-shot examples.
    Question(
        dimension="example_explainer",
        prompt=(
            "Write a short post (2-4 sentences) the way you actually would, "
            "explaining one technical idea you know well to a smart non-expert."
        ),
        kind="generative",
    ),
    Question(
        dimension="example_disagreement",
        prompt=(
            "Write a short post (2-4 sentences) where you politely but clearly "
            "disagree with a popular opinion in your field."
        ),
        kind="generative",
    ),
    Question(
        dimension="example_apology",
        prompt=(
            "Write a short post (2-4 sentences) acknowledging that you got "
            "something wrong publicly. Use your real voice."
        ),
        kind="generative",
    ),
]


QUICK_DIMENSIONS = {
    "style",
    "humor",
    "values",
    "banned_phrases",
    "example_explainer",
    "example_disagreement",
}


def all_questions() -> list[Question]:
    return list(QUESTION_BANK)


def required_questions() -> list[Question]:
    return [q for q in QUESTION_BANK if q.required]


def quick_questions() -> list[Question]:
    """Smaller, fast question set: ~6 questions instead of ~17."""
    seen: set[str] = set()
    out: list[Question] = []
    for q in QUESTION_BANK:
        if q.dimension in QUICK_DIMENSIONS and q.dimension not in seen:
            out.append(q)
            seen.add(q.dimension)
    return out


def by_dimension(dimension: str) -> list[Question]:
    return [q for q in QUESTION_BANK if q.dimension == dimension]
