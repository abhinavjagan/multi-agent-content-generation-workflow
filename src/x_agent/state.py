"""Graph state definition for the x-agent LangGraph."""

from __future__ import annotations

from typing import Literal, TypedDict


PostMode = Literal["single", "thread"]


class AgentState(TypedDict, total=False):
    """State threaded through every node of the graph.

    Fields are optional (``total=False``) because they're populated as the graph
    progresses. Only ``topic`` and ``mode`` are required at invocation time.
    """

    topic: str
    style: str
    mode: PostMode
    model: str | None

    draft: str
    posts: list[str]

    finalized: bool
    rejected: bool

    error: str | None

    # --- Persona conditioning (optional; set when --persona is used) ---
    persona_id: str | None
    persona: dict | None
    retrieved_examples: list[str]
    critic_score: int
    critic_violations: list[str]
    critic_suggestion: str
    critic_attempts: int

    # --- Draft generation overrides (optional) ---
    # When ``seed_posts`` is non-empty, ``generate_draft`` skips the LLM and
    # treats the joined text as the draft. Used by the "pick a variant" flow
    # so the user's chosen variant enters the normal HITL pipeline without a
    # second (different) generation.
    seed_posts: list[str] | None
    # Optional override for the LLM sampling temperature. Used by the
    # variants endpoint to fan out N parallel generations with diverse temps
    # (e.g. 0.6 / 0.8 / 1.0) so the user gets meaningfully different drafts.
    temperature: float | None

    # --- Web research (optional) ---
    # When ``research_enabled`` is True the ``research`` node fetches URLs
    # (if ``research_urls`` is non-empty) or searches the web for
    # ``research_query`` (defaults to ``topic``). Results land in
    # ``web_results`` as a list of ``WebResult.model_dump()`` dicts so the
    # state stays JSON-serialisable for the LangGraph checkpointer.
    research_enabled: bool
    research_urls: list[str] | None
    research_query: str | None
    web_results: list[dict]
