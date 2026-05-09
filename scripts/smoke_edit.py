"""Verify the human-in-the-loop edit path: draft -> edit -> re-format -> approve."""

from __future__ import annotations

import os
import uuid

os.environ["X_AGENT_FORCE_DRY_RUN"] = "1"

from langgraph.types import Command  # noqa: E402

from x_agent.graph import build_graph  # noqa: E402


def main() -> int:
    graph = build_graph()
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}

    state = graph.invoke(
        {
            "topic": "How to size a thread pool for an IO-bound web service",
            "mode": "thread",
            "model": "llama3:latest",
        },
        config=cfg,
    )

    interrupts = state.get("__interrupt__")
    assert interrupts, "graph should have paused for review"
    original = interrupts[0].value["posts"]
    print(f"== first draft ({len(original)} tweet(s)) ==")
    for i, p in enumerate(original, 1):
        print(f"  {i}/{len(original)} ({len(p)}): {p[:80]}{'...' if len(p) > 80 else ''}")
    print()

    # User edits the post: replace with our own short two-paragraph blog.
    edited = (
        "Rule of thumb: for IO-bound services, start at 2 * CPU * (1 + wait/compute).\n\n"
        "If your average request spends 90 percent waiting and 10 percent computing,\n"
        "that ratio is 9, so 8 cores -> 160 worker threads. Measure p99 latency and tail "
        "queue depth before you trust the formula."
    )

    state = graph.invoke(
        Command(resume={"action": "edit", "edited": edited}),
        config=cfg,
    )

    # The edit path routes back through format_for_x then re-pauses on review.
    interrupts = state.get("__interrupt__")
    assert interrupts, "after edit the graph should pause again for review"
    posts = interrupts[0].value["posts"]
    print(f"== after edit, re-formatted ({len(posts)} tweet(s)) ==")
    for i, p in enumerate(posts, 1):
        print(f"  {i}/{len(posts)} ({len(p)}): {p}")
    print()

    state = graph.invoke(Command(resume={"action": "approve"}), config=cfg)
    print("== final state ==")
    for k in ("approved", "tweet_ids", "tweet_url", "error"):
        print(f"  {k}: {state.get(k)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
