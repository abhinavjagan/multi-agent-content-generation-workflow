"""End-to-end smoke test: real Ollama call, auto-approve, print final draft.

x-agent never publishes anywhere -- `approve` simply finalizes the draft
and the graph ends.

Run with: python scripts/smoke.py "<topic>" [single|thread]
"""

from __future__ import annotations

import os
import sys
import uuid

from langgraph.types import Command

from x_agent.graph import build_graph


def main() -> int:
    topic = sys.argv[1] if len(sys.argv) > 1 else "Why local LLMs matter in 2026"
    mode = sys.argv[2] if len(sys.argv) > 2 else "thread"
    model = os.environ.get("OLLAMA_MODEL", "llama3:latest")

    graph = build_graph()
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}

    print(f"== topic: {topic}\n== mode:  {mode}\n== model: {model}\n")

    state = graph.invoke(
        {"topic": topic, "mode": mode, "model": model},
        config=cfg,
    )

    interrupts = state.get("__interrupt__")
    if not interrupts:
        print("ERROR: graph did not pause for review")
        print(state)
        return 1

    payload = interrupts[0].value
    posts = payload["posts"]

    print(f"== draft ({len(posts)} tweet(s)) ==\n")
    for i, body in enumerate(posts, start=1):
        print(f"--- tweet {i}/{len(posts)}  [{len(body)} chars] ---")
        print(body)
        print()

    print("== raw draft from LLM ==")
    print(state.get("draft", "<missing>"))
    print()

    state = graph.invoke(Command(resume={"action": "approve"}), config=cfg)
    print("== final state ==")
    for k in ("finalized", "rejected", "error"):
        print(f"  {k}: {state.get(k)}")
    print(f"  posts: {len(state.get('posts') or [])} tweet(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
