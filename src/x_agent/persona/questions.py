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
        dimension="cadence",
        prompt=(
            "When you read your own writing out loud, what does the rhythm "
            "feel like? Short staccato beats? Long winding sentences? Do you "
            "lean on em-dashes, ellipses, comma run-ons? Give me two real "
            "sentences that show your cadence."
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
    Question(
        dimension="conviction_signals",
        prompt=(
            "When you actually believe something hard, how do you say so? "
            "Give me the phrases you'd use to plant a flag (e.g. 'I'll die "
            "on this hill', 'no notes', 'full stop', 'this is the take')."
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
        dimension="pet_peeves",
        prompt=(
            "Beyond words, what behaviors in posts/replies make you physically "
            "wince? (e.g. 'reply guys', 'screenshots of LinkedIn', 'corporate "
            "vagueposting'). List 3-5 with one line on each."
        ),
    ),
    Question(
        dimension="signature_phrases",
        prompt=(
            "Are there phrases or constructions you use a lot that feel like "
            "yours? List a few if so."
        ),
    ),
    Question(
        dimension="idioms",
        prompt=(
            "Give me five short verbatim quirks of yours: a favorite opener, "
            "a favorite closer, a word you misspell on purpose, a piece of "
            "slang only your friend group uses, a weird grammar habit. Write "
            "each one exactly the way you'd type it."
        ),
    ),
    # Stories you reach for
    Question(
        dimension="stories",
        prompt=(
            "What are 2-3 stories or anecdotes you reach for over and over "
            "when you're trying to make a point? One or two sentences for "
            "each is fine."
        ),
    ),
    # Emotional range + enthusiasm
    Question(
        dimension="enthusiasm_tells",
        prompt=(
            "How does the world know you're genuinely excited about something? "
            "Caps, punctuation, specific words, screenshots, gifs? Give me the "
            "actual tells."
        ),
    ),
    Question(
        dimension="emotional_range",
        prompt=(
            "What's the emotional range you let yourself show in public? "
            "Are there moods you keep private? Where's the line between "
            "'me on a good day' and 'me when I'm wound up'?"
        ),
    ),
    Question(
        dimension="apology_pattern",
        prompt=(
            "When you mess up publicly, what's the shape of your apology? "
            "Walk me through the structure: do you lead with what happened, "
            "with sorry, with a fix? What do you NOT do?"
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
    Question(
        dimension="example_announce",
        prompt=(
            "Write a short post (2-4 sentences) the way you'd announce "
            "something you're proud of without sounding salesy."
        ),
        kind="generative",
    ),
    Question(
        dimension="example_excited",
        prompt=(
            "Write a short post (2-4 sentences) the way you'd talk about "
            "something that genuinely lit you up this week. Show the "
            "enthusiasm, don't fake it."
        ),
        kind="generative",
    ),
    Question(
        dimension="example_morning_pages",
        prompt=(
            "Write 2-4 sentences in your voice that aren't trying to be "
            "anything - just how you'd think out loud about your morning. "
            "Loose, unfiltered, the way you actually sound."
        ),
        kind="generative",
    ),
]


QUICK_DIMENSIONS = {
    "style",
    "cadence",
    "humor",
    "values",
    "banned_phrases",
    "idioms",
    "example_explainer",
    "example_morning_pages",
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
