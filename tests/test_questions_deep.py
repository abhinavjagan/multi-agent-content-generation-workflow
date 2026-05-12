"""Unit tests for Deep-mode question bank + interview helpers."""

from __future__ import annotations

from unittest.mock import patch

from x_agent.interview_graph import (
    InterviewState,
    _bank,
    _followup_cap,
    _resolve_mode,
    ask_next_question,
    initial_interview_state,
    judge_followup,
    MAX_FOLLOWUPS_PER_DIMENSION,
    MAX_FOLLOWUPS_PER_DIMENSION_DEEP,
)
from x_agent.persona.questions import (
    DEEP_ONLY_QUESTIONS,
    QUESTION_BANK,
    all_questions,
    by_dimension,
    deep_questions,
    quick_questions,
)


# ----------------------------------------------------------- bank composition


def test_deep_questions_includes_full_bank_plus_extras():
    full = all_questions()
    deep = deep_questions()
    assert len(deep) == len(full) + len(DEEP_ONLY_QUESTIONS)
    # The default bank stays in the same order at the top.
    for orig, d in zip(full, deep):
        assert orig.prompt == d.prompt
        assert orig.dimension == d.dimension


def test_deep_only_dimensions_are_new():
    bank_dims = {q.dimension for q in QUESTION_BANK}
    deep_dims = {q.dimension for q in DEEP_ONLY_QUESTIONS}
    overlap = bank_dims & deep_dims
    assert overlap == set(), (
        f"deep-only questions should introduce new dimensions; overlap={overlap}"
    )


def test_deep_questions_count_is_substantial():
    """Sanity: deep mode is materially longer than default."""
    assert len(deep_questions()) >= len(all_questions()) + 10


def test_quick_questions_unchanged():
    """Quick mode must still be the ~6-question fast path."""
    qs = quick_questions()
    assert 5 <= len(qs) <= 10
    # Deep-only dimensions never leak into quick.
    deep_dims = {q.dimension for q in DEEP_ONLY_QUESTIONS}
    for q in qs:
        assert q.dimension not in deep_dims


def test_by_dimension_finds_deep_only():
    """by_dimension() must search both QUESTION_BANK and DEEP_ONLY_QUESTIONS."""
    sample = DEEP_ONLY_QUESTIONS[0]
    found = by_dimension(sample.dimension)
    assert any(q.prompt == sample.prompt for q in found)


# -------------------------------------------------------- mode resolution glue


def test_resolve_mode_default():
    assert _resolve_mode(None) == "default"
    assert _resolve_mode({}) == "default"


def test_resolve_mode_explicit_wins_over_quick_flag():
    state: InterviewState = {"quick": True, "mode": "deep"}
    assert _resolve_mode(state) == "deep"


def test_resolve_mode_legacy_quick():
    state: InterviewState = {"quick": True}
    assert _resolve_mode(state) == "quick"


def test_bank_switches_on_mode():
    quick_state: InterviewState = {"mode": "quick"}
    default_state: InterviewState = {"mode": "default"}
    deep_state: InterviewState = {"mode": "deep"}
    assert len(_bank(quick_state)) == len(quick_questions())
    assert len(_bank(default_state)) == len(all_questions())
    assert len(_bank(deep_state)) == len(deep_questions())


def test_followup_cap_higher_in_deep():
    assert _followup_cap({"mode": "default"}) == MAX_FOLLOWUPS_PER_DIMENSION
    assert _followup_cap({"mode": "deep"}) == MAX_FOLLOWUPS_PER_DIMENSION_DEEP
    assert MAX_FOLLOWUPS_PER_DIMENSION_DEEP > MAX_FOLLOWUPS_PER_DIMENSION


# ----------------------------------------------------- initial_interview_state


def test_initial_state_carries_mode():
    s = initial_interview_state(
        name="Abhi",
        is_real_person=False,
        disclosure_text="",
        consent_ack=False,
        mode="deep",
    )
    assert s["mode"] == "deep"
    assert s["quick"] is False


def test_initial_state_back_compat_quick():
    s = initial_interview_state(
        name="Abhi",
        is_real_person=False,
        disclosure_text="",
        consent_ack=False,
        quick=True,
    )
    assert s["mode"] == "quick"
    assert s["quick"] is True


def test_initial_state_default_when_neither():
    s = initial_interview_state(
        name="Abhi",
        is_real_person=False,
        disclosure_text="",
        consent_ack=False,
    )
    assert s["mode"] == "default"


# ---------------------------------------------------- aggressive judge fallback


def test_judge_short_answer_in_aggressive_demands_concrete():
    """Aggressive judge raises the min-length and changes the follow-up."""
    from x_agent.persona.interview import judge_answer_completeness
    from x_agent.persona.questions import Question

    q = Question(dimension="values", prompt="What do you value?", kind="open")
    short = "I value clarity."  # 16 chars

    plain = judge_answer_completeness(question=q, answer=short, aggressive=False)
    deep = judge_answer_completeness(question=q, answer=short, aggressive=True)

    # Plain mode might let 16 chars through to the LLM (>= 30 only). With
    # 16 chars BOTH should mark too_short, but aggressive uses the longer
    # follow-up phrasing.
    assert deep["sufficient"] is False
    assert deep["reason"] == "too_short"
    # Aggressive follow-up explicitly asks for a real example.
    assert "real example" in (deep.get("followup") or "")
    # Plain follow-up is the older "expand on that" wording.
    if plain["reason"] == "too_short":
        assert "expand" in (plain.get("followup") or "").lower()


# ----------------------------- regression: follow-up routing at idx=0
#
# Regression test for the "stuck on Q1" bug. When the judge decides the
# answer to the very first question (idx=0) deserves a follow-up, the
# graph used to skip the follow-up branch (the guard was ``idx > 0``)
# and silently re-ask Q1 with the same prompt and ``is_followup=False``.
# That made the UI loop on Q1 unless the user pressed Skip.


def test_judge_followup_keeps_idx_when_followup_needed():
    """When the judge wants a follow-up, idx must NOT advance."""
    bank = all_questions()
    q1 = bank[0]
    state: InterviewState = {
        "mode": "default",
        "question_index": 0,
        "transcript": [
            {
                "dimension": q1.dimension,
                "question": q1.prompt,
                "answer": "ok",
                "is_followup": False,
                "is_holdout": False,
            }
        ],
        "pending_question": {
            "dimension": q1.dimension,
            "prompt": q1.prompt,
            "kind": q1.kind,
            "is_followup": False,
        },
        "followups_used": {},
    }
    with patch(
        "x_agent.interview_graph.judge_answer_completeness",
        return_value={
            "sufficient": False,
            "reason": "too_short",
            "followup": "Can you give a concrete example?",
        },
    ):
        cmd = judge_followup(state)
    # judge_followup is a Command(update=..., goto=...)
    update = cmd.update or {}
    assert update.get("pending_followup") == "Can you give a concrete example?"
    # CRITICAL: idx must stay at 0 so ask_next_question knows we're still
    # on the first bank entry and can route the follow-up correctly.
    assert "question_index" not in update or update["question_index"] == 0
    assert cmd.goto == "ask_next_question"


def test_ask_next_question_routes_followup_at_idx_zero():
    """The follow-up to Q1 must be asked verbatim, not silently dropped.

    Regression: previously the guard was ``idx > 0`` and the code reached
    for ``bank[idx - 1]``, so a follow-up at idx=0 silently re-asked Q1
    with ``is_followup=False`` -- the source of the "stuck on Q1" loop.
    """
    bank = all_questions()
    q1 = bank[0]
    followup_text = "What does that look like on a Monday morning?"
    state: InterviewState = {
        "mode": "default",
        "question_index": 0,
        "pending_followup": followup_text,
        "transcript": [
            {
                "dimension": q1.dimension,
                "question": q1.prompt,
                "answer": "ok",
                "is_followup": False,
                "is_holdout": False,
            }
        ],
        "followups_used": {q1.dimension: 1},
    }

    # ``ask_next_question`` calls LangGraph's ``interrupt(...)`` to pause
    # the graph. Outside an active graph the real ``interrupt`` raises
    # an unrelated RuntimeError, so we monkeypatch it to capture the
    # payload (the question dict the UI will receive) and short-circuit
    # the function. The captured payload is what we assert against.
    class _StopForTest(Exception):
        pass

    captured: dict = {}

    def fake_interrupt(payload):
        captured.update(payload)
        # Raise a benign exception to unwind out of ask_next_question
        # without exercising the transcript-persist tail.
        raise _StopForTest()

    with patch("x_agent.interview_graph.interrupt", side_effect=fake_interrupt):
        try:
            ask_next_question(state)
        except _StopForTest:
            pass
        else:  # pragma: no cover - the patched interrupt MUST fire
            raise AssertionError(
                "ask_next_question did not call interrupt() for a follow-up"
            )

    assert captured.get("kind") == "interview_question"
    q = captured["question"]
    assert q["is_followup"] is True, "follow-up must be marked as such"
    assert q["prompt"] == followup_text, (
        f"follow-up prompt must be the queued text, not Q1's prompt; got {q['prompt']!r}"
    )
    assert q["dimension"] == q1.dimension
