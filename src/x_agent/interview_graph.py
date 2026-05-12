"""Phase A: interview graph that captures a persona by conversation.

State machine:

    START -> ask_next_question -> [interrupt for human answer]
          -> record_answer -> judge_followup
          -> (followup) -> ask_next_question
          -> (advance)  -> ask_next_question (until done)
          -> extract -> embed_and_save -> END

The graph PAUSES on every question via ``interrupt()``. The CLI / API
resumes it with ``Command(resume={"answer": "<text>"})``.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .persona.embedder import PersonaEmbedder
from .persona.interview import (
    extract_persona_spec,
    judge_answer_completeness,
)
from .persona.questions import all_questions, deep_questions, quick_questions
from .persona.schema import (
    PersonaSpec,
    TranscriptEntry,
    new_persona_id,
    utcnow,
)
from .persona.store import get_default_store

log = logging.getLogger(__name__)


InterviewMode = Literal["quick", "default", "deep"]


MAX_FOLLOWUPS_PER_DIMENSION = 2
MAX_FOLLOWUPS_PER_DIMENSION_DEEP = 3


class InterviewState(TypedDict, total=False):
    persona_id: str
    name: str
    is_real_person: bool
    disclosure_text: str
    consent_ack: bool
    # Legacy boolean kept for back-compat. New callers should set ``mode``.
    quick: bool
    # 'quick' (~6 Qs), 'default' (~26 Qs), 'deep' (~40 Qs + aggressive follow-ups).
    mode: InterviewMode

    question_index: int
    pending_question: dict | None
    pending_followup: str | None
    followups_used: dict[str, int]

    transcript: list[dict]

    persona: dict | None
    saved: bool
    error: str | None


# ----------------------------------------------------------------------- nodes


def _resolve_mode(state: InterviewState | None) -> InterviewMode:
    """Map the (mode, quick) inputs to a single canonical mode.

    Back-compat: ``quick=True`` without an explicit ``mode`` still works
    and is treated as ``mode='quick'``. An explicit ``mode`` always wins.
    """
    if not state:
        return "default"
    mode = state.get("mode")
    if mode in ("quick", "default", "deep"):
        return mode  # type: ignore[return-value]
    if state.get("quick"):
        return "quick"
    return "default"


def _bank(state: InterviewState | None = None) -> list:
    mode = _resolve_mode(state)
    if mode == "quick":
        return quick_questions()
    if mode == "deep":
        return deep_questions()
    return all_questions()


def _followup_cap(state: InterviewState | None) -> int:
    return (
        MAX_FOLLOWUPS_PER_DIMENSION_DEEP
        if _resolve_mode(state) == "deep"
        else MAX_FOLLOWUPS_PER_DIMENSION
    )


def ask_next_question(state: InterviewState) -> Command:
    """Ask either a queued follow-up or the next bank question, then pause.

    ``question_index`` always points at the *current* bank entry -- the one
    we are about to ask, or the one we just answered while ``pending_followup``
    is queued. ``judge_followup`` only advances the index when it decides the
    answer was sufficient, so a queued follow-up shares the dimension /
    kind of ``bank[idx]`` (not ``bank[idx - 1]``).
    """
    bank = _bank(state)
    idx = int(state.get("question_index", 0))
    pending_followup = state.get("pending_followup")
    transcript = list(state.get("transcript") or [])

    if pending_followup is not None:
        # Follow-up belongs to the dimension of the question we just
        # answered, which is at bank[idx]. Clamp defensively in case
        # idx has somehow run off the end (it shouldn't -- judge_followup
        # routes to extract once we're past the bank).
        base = bank[min(idx, len(bank) - 1)]
        question = {
            "dimension": base.dimension,
            "prompt": pending_followup,
            "kind": base.kind,
            "is_followup": True,
        }
    else:
        if idx >= len(bank):
            return Command(goto="extract")
        q = bank[idx]
        question = {
            "dimension": q.dimension,
            "prompt": q.prompt,
            "kind": q.kind,
            "is_followup": False,
        }

    answer = interrupt({
        "kind": "interview_question",
        "question_index": idx,
        "total": len(bank),
        "question": question,
    })

    if not isinstance(answer, dict):
        answer_text = str(answer or "").strip()
    else:
        answer_text = str(answer.get("answer", "")).strip()
    if not answer_text:
        # Treat empty as skip; record an empty entry and advance.
        answer_text = "(skipped)"

    entry = {
        "dimension": question["dimension"],
        "question": question["prompt"],
        "answer": answer_text,
        "is_followup": question["is_followup"],
        "is_holdout": False,
    }
    transcript.append(entry)

    # Persist progressively to disk so a refresh / server crash does not
    # lose interview answers. The directory + spec placeholder are
    # created lazily on the first answer; subsequent answers append to
    # the existing transcript.jsonl. Best-effort: failures don't block
    # the interview, they're just logged.
    persona_id = state.get("persona_id")
    if persona_id:
        try:
            store = get_default_store()
            if not store.exists(persona_id):
                name = (state.get("name") or "").strip() or "subject"
                is_real = bool(state.get("is_real_person", True))
                placeholder = PersonaSpec(
                    id=persona_id,
                    name=name,
                    is_real_person=is_real,
                    consent_recorded_at=utcnow() if is_real else None,
                    disclosure_text=(state.get("disclosure_text") or "").strip(),
                )
                store.save(placeholder)
            store.append_transcript(
                persona_id, TranscriptEntry.model_validate(entry)
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            log.warning("interview.persist_answer failed: %s", exc)

    return Command(
        update={
            "transcript": transcript,
            "pending_question": question,
            "pending_followup": None,
        },
        goto="judge_followup",
    )


def judge_followup(state: InterviewState) -> Command:
    """Decide whether to ask a follow-up for the question we just answered."""
    bank = _bank(state)
    transcript = list(state.get("transcript") or [])
    pending = state.get("pending_question") or {}
    followups_used = dict(state.get("followups_used") or {})
    idx = int(state.get("question_index", 0))

    if not transcript:
        return Command(goto="ask_next_question")

    last = transcript[-1]
    dimension = pending.get("dimension", last.get("dimension", "unknown"))
    used = int(followups_used.get(dimension, 0))

    if last.get("is_followup") or pending.get("kind") == "generative":
        followups_used[dimension] = used  # unchanged
        return Command(
            update={
                "question_index": idx + 1,
                "pending_question": None,
                "followups_used": followups_used,
                "pending_followup": None,
            },
            goto=("extract" if idx + 1 >= len(bank) else "ask_next_question"),
        )

    if used >= _followup_cap(state):
        return Command(
            update={
                "question_index": idx + 1,
                "pending_question": None,
                "followups_used": followups_used,
                "pending_followup": None,
            },
            goto=("extract" if idx + 1 >= len(bank) else "ask_next_question"),
        )

    # Build a synthetic Question to feed the judge.
    from .persona.questions import Question

    q = Question(
        dimension=dimension,
        prompt=pending.get("prompt", ""),
        kind=pending.get("kind", "open"),
    )
    judgement = judge_answer_completeness(
        question=q,
        answer=last["answer"],
        aggressive=(_resolve_mode(state) == "deep"),
    )
    if judgement.get("sufficient", True) or not judgement.get("followup"):
        return Command(
            update={
                "question_index": idx + 1,
                "pending_question": None,
                "followups_used": followups_used,
                "pending_followup": None,
            },
            goto=("extract" if idx + 1 >= len(bank) else "ask_next_question"),
        )

    followups_used[dimension] = used + 1
    return Command(
        update={
            "pending_followup": str(judgement["followup"]),
            "pending_question": None,
            "followups_used": followups_used,
        },
        goto="ask_next_question",
    )


def extract(state: InterviewState) -> dict[str, Any]:
    """Run the LLM extractor over the transcript -> ``PersonaSpec`` dict.

    Persists the raw transcript to disk *before* invoking the LLM so that a
    crash in the extractor (e.g. wrong Ollama model name, network blip) never
    loses interview answers. The user can then re-run extraction with
    ``x-agent persona resume-extract <persona-id>``.
    """
    name = (state.get("name") or "").strip() or "subject"
    persona_id = state.get("persona_id") or new_persona_id(name)
    is_real_person = bool(state.get("is_real_person", True))
    disclosure_text = (state.get("disclosure_text") or "").strip()
    if is_real_person and not disclosure_text:
        return {
            "error": (
                "is_real_person=true requires disclosure_text "
                "(e.g. '[AI persona of @handle]')"
            ),
        }
    if is_real_person and not bool(state.get("consent_ack")):
        return {"error": "consent must be acknowledged for real-person personas"}

    transcript_entries = [
        TranscriptEntry.model_validate(t) for t in (state.get("transcript") or [])
    ]
    if not transcript_entries:
        return {"error": "no transcript entries to extract"}

    # 1) PERSIST TRANSCRIPT FIRST so a crash in extraction is recoverable.
    #    We write a minimal placeholder spec alongside it so the persona id
    #    appears in `persona list` and refers to a real on-disk directory.
    store = get_default_store()
    placeholder = PersonaSpec(
        id=persona_id,
        name=name,
        is_real_person=is_real_person,
        consent_recorded_at=utcnow() if is_real_person else None,
        disclosure_text=disclosure_text,
    )
    try:
        store.save(placeholder)
        store.overwrite_transcript(persona_id, transcript_entries)
        log.info(
            "interview.extract pre-saved transcript id=%s entries=%d",
            persona_id, len(transcript_entries),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort, don't block extract
        log.warning("interview.extract pre-save failed: %s", exc)

    # 2) Now run the LLM extractor. If this raises, the transcript above is
    #    already on disk and the user can resume.
    spec = extract_persona_spec(
        name=name,
        is_real_person=is_real_person,
        disclosure_text=disclosure_text,
        transcript=transcript_entries,
        persona_id=persona_id,
    )
    log.info("interview.extract id=%s entries=%d", spec.id, len(transcript_entries))
    return {"persona": spec.model_dump(mode="json"), "persona_id": spec.id}


def embed_and_save(state: InterviewState) -> dict[str, Any]:
    """Persist spec + transcript + embeddings to the persona store."""
    persona = state.get("persona")
    if not persona:
        return {"error": "embed_and_save called without a persona"}
    spec = PersonaSpec.model_validate(persona)
    transcript_entries = [
        TranscriptEntry.model_validate(t) for t in (state.get("transcript") or [])
    ]

    store = get_default_store()
    store.save(spec)
    store.overwrite_transcript(spec.id, transcript_entries)

    if transcript_entries:
        embedder = PersonaEmbedder()
        try:
            ids, _texts, vectors = embedder.build_index(transcript_entries)
            if vectors.size > 0:
                store.save_embeddings(spec.id, ids, vectors)
        except Exception as exc:  # noqa: BLE001 - embedding is best-effort
            log.warning("interview.embed failed (continuing): %s", exc)

    return {"saved": True}


# ----------------------------------------------------------------------- graph


def build_interview_graph() -> Any:
    g = StateGraph(InterviewState)

    g.add_node("ask_next_question", ask_next_question)
    g.add_node("judge_followup", judge_followup)
    g.add_node("extract", extract)
    g.add_node("embed_and_save", embed_and_save)

    g.add_edge(START, "ask_next_question")
    g.add_edge("extract", "embed_and_save")
    g.add_edge("embed_and_save", END)

    return g.compile(checkpointer=MemorySaver())


def initial_interview_state(
    *,
    name: str,
    is_real_person: bool,
    disclosure_text: str,
    consent_ack: bool,
    persona_id: str | None = None,
    quick: bool = False,
    mode: InterviewMode | None = None,
) -> InterviewState:
    # Back-compat: explicit ``mode`` always wins; ``quick=True`` collapses
    # to ``mode='quick'`` when nothing else was provided.
    resolved: InterviewMode = mode if mode in ("quick", "default", "deep") else (
        "quick" if quick else "default"
    )
    return InterviewState(
        persona_id=persona_id or new_persona_id(name),
        name=name,
        is_real_person=is_real_person,
        disclosure_text=disclosure_text,
        consent_ack=consent_ack,
        quick=(resolved == "quick"),
        mode=resolved,
        question_index=0,
        pending_followup=None,
        followups_used={},
        transcript=[],
    )


__all__ = [
    "InterviewMode",
    "InterviewState",
    "build_interview_graph",
    "initial_interview_state",
    "MAX_FOLLOWUPS_PER_DIMENSION",
    "MAX_FOLLOWUPS_PER_DIMENSION_DEEP",
]
