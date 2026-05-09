"""Assemble the LangGraph state machine."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .nodes import (
    format_for_x,
    generate_draft,
    human_review,
    load_persona,
    persona_critic,
    post_to_x,
    research,
    retrieve_examples,
)
from .state import AgentState


def build_graph() -> Any:
    """Build and compile the agent graph with an in-memory checkpointer.

    Flow (persona path takes the same nodes; persona-aware nodes are no-ops
    when ``persona_id`` is not set in state). The ``research`` node sits
    between persona retrieval and draft generation -- when
    ``research_enabled`` is False (the default for every existing call
    path) it short-circuits to an empty ``web_results`` and the rest of
    the graph behaves exactly as before.

        START -> load_persona -> retrieve_examples -> research
              -> generate_draft -> format_for_x -> persona_critic
              -> human_review -> post_to_x

    ``persona_critic`` uses ``Command(goto=...)`` to either advance to
    ``human_review`` or loop back to ``generate_draft`` (skipping
    ``research`` -- the prior fetched context is reused on retries).
    ``human_review`` uses ``Command(goto=...)`` to dispatch to
    ``post_to_x``, ``generate_draft``, ``format_for_x``, or END based on
    the user's choice.
    """
    graph = StateGraph(AgentState)

    graph.add_node("load_persona", load_persona)
    graph.add_node("retrieve_examples", retrieve_examples)
    graph.add_node("research", research)
    graph.add_node("generate_draft", generate_draft)
    graph.add_node("format_for_x", format_for_x)
    graph.add_node("persona_critic", persona_critic)
    graph.add_node("human_review", human_review)
    graph.add_node("post_to_x", post_to_x)

    graph.add_edge(START, "load_persona")
    graph.add_edge("load_persona", "retrieve_examples")
    graph.add_edge("retrieve_examples", "research")
    graph.add_edge("research", "generate_draft")
    graph.add_edge("generate_draft", "format_for_x")
    graph.add_edge("format_for_x", "persona_critic")
    graph.add_edge("post_to_x", END)

    return graph.compile(checkpointer=MemorySaver())
