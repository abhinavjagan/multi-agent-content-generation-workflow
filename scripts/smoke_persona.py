"""End-to-end persona smoke test.

What this exercises:
1. Canned interview answers fed through the interview graph.
2. The extractor LLM call building a PersonaSpec.
3. The persona-conditioned writer + retrieval + critic loop.
4. Disclosure auto-injection for is_real_person personas.
5. Dry-run X post.

Requires a local Ollama with both a chat model (OLLAMA_MODEL or
``llama3:latest``) and the embedding model ``nomic-embed-text``::

    ollama pull llama3:latest
    ollama pull nomic-embed-text
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

os.environ["X_AGENT_FORCE_DRY_RUN"] = "1"

# Use an isolated persona dir so we don't pollute the user's real one.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="x-agent-smoke-"))
os.environ["PERSONA_DIR"] = str(_TMP_DIR)

# Reset the cached settings + store so the env override above takes effect.
from x_agent import config as _config_mod  # noqa: E402
_config_mod.get_settings.cache_clear()
from x_agent.persona import store as _store_mod  # noqa: E402
_store_mod.reset_default_store()

from langgraph.types import Command  # noqa: E402

from x_agent.graph import build_graph  # noqa: E402
from x_agent.interview_graph import (  # noqa: E402
    build_interview_graph,
    initial_interview_state,
)


CANNED_ANSWERS = {
    # Match the order of QUESTION_BANK in src/x_agent/persona/questions.py
    "style:0": (
        "To friends I write like 'lol no, that's not how that works'. "
        "To colleagues I'd say 'I'm skeptical of that claim because we don't "
        "have data on tail-latency impact yet.'"
    ),
    "brevity:0": (
        "One short sentence first, then if context is needed I add it as a "
        "second sentence. I hate posts that bury the point in paragraph two."
    ),
    "humor:0": (
        "Dry and a bit self-deprecating. Mostly when I'm explaining something "
        "obvious in retrospect. Never punching down."
    ),
    "humor:1": (  # holdout
        "I once said 'monorepos are great until they aren't, which is around 11am Tuesday'."
    ),
    "values:0": (
        "Clarity over cleverness, ship the boring thing first, and respect for "
        "the on-call person tomorrow morning."
    ),
    "opinions:0": (
        "Boring tech over shiny tech for production. Long-running services over "
        "serverless when latency tails matter."
    ),
    "boundaries:0": (
        "I won't post about specific people, internal politics, or anything that "
        "would feel like a hot take when I'm tired."
    ),
    "banned_phrases:0": (
        "leverage, unlock, game-changer, ecosystem, paradigm, journey, deep dive."
    ),
    "signature_phrases:0": (
        "I often start with 'Quick note:' and I tend to end with the actionable "
        "thing in one short sentence."
    ),
    "domains:0": (
        "Distributed systems, observability (Prometheus/OpenTelemetry), and "
        "Python tooling for backend services."
    ),
    "topics_loved:0": (
        "Anything about long-tail latency, on-call ergonomics, or post-mortems."
    ),
    "topics_avoided:0": (
        "Crypto, generic productivity advice, anything tribal about editors."
    ),
    "decision_style:0": (
        "I list the two riskiest unknowns. Pick the option that fails most "
        "loudly. Decide in writing so I can revisit later."
    ),
    "confidence_phrasing:0": (
        "Phrases like 'I'd guess', 'this is hand-wavy but', 'someone correct me "
        "if I'm wrong here'."
    ),
    "example_explainer:0": (
        "Quick note on idempotency: design write endpoints so retrying with the "
        "same input produces the same result. The classic trick is a client-"
        "supplied request id you de-dupe on. Saves you during partial failures."
    ),
    "example_disagreement:0": (
        "Counter-take: 'just use microservices' is rarely the answer if you "
        "haven't measured your actual coupling. Most teams I've seen would do "
        "better with a well-organized monolith and a clean module boundary."
    ),
    "example_apology:0": (
        "Quick note: I shipped a regression in the auth refresh path on Friday "
        "and didn't catch it in canary. Fix is in, post-mortem coming. Sorry "
        "to anyone who got paged."
    ),
}


def feed_interview() -> str:
    graph = build_interview_graph()
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
    state = graph.invoke(
        dict(initial_interview_state(
            name="Abhi",
            is_real_person=True,
            disclosure_text="[AI persona of @abhi - drafted by AI]",
            consent_ack=True,
        )),
        config=cfg,
    )
    asked = 0
    while True:
        interrupts = state.get("__interrupt__")
        if not interrupts:
            break
        payload = interrupts[0].value
        if payload.get("kind") != "interview_question":
            break
        q = payload["question"]
        idx = int(payload.get("question_index", 0))
        key = f"{q['dimension']}:{idx}"
        # If the bank evolves, fall back to a generic answer rather than crash.
        answer = CANNED_ANSWERS.get(
            key,
            "Yes, that matches how I'd think about it. Concrete example: "
            "I'd default to the boring, well-understood option.",
        )
        if q.get("is_followup"):
            answer = "Same as above plus: I usually add a short example to make it concrete."
        print(f"[interview] q{idx + 1} ({q['dimension']}): {q['prompt'][:70]}...")
        state = graph.invoke(Command(resume={"answer": answer}), config=cfg)
        asked += 1

    if state.get("error"):
        raise SystemExit(f"interview failed: {state['error']}")
    persona_id = state.get("persona_id") or (state.get("persona") or {}).get("id")
    print(f"[interview] saved persona id={persona_id} after {asked} questions")
    return persona_id


def draft_with_persona(persona_id: str, topic: str) -> dict:
    graph = build_graph()
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
    state = graph.invoke(
        {"topic": topic, "mode": "single", "persona_id": persona_id, "model": os.environ.get("OLLAMA_MODEL", "llama3:latest")},
        config=cfg,
    )
    interrupts = state.get("__interrupt__")
    if not interrupts:
        raise SystemExit("expected the graph to pause for review")
    payload = interrupts[0].value
    posts = payload["posts"]
    print(f"\n[draft] topic: {topic}")
    print(f"[draft] critic_score: {payload.get('critic_score')}")
    print(f"[draft] critic_violations: {payload.get('critic_violations')}")
    print("[draft] body:")
    for i, p in enumerate(posts, 1):
        print(f"  ({len(p)} chars) {p}")

    state = graph.invoke(Command(resume={"action": "approve"}), config=cfg)
    return state


def main() -> int:
    try:
        persona_id = feed_interview()
        # Make a few drafts on different topics to exercise the critic loop.
        topics = [
            "what makes a good distributed-tracing span name",
            "rolling out a database schema change without downtime",
        ]
        for topic in topics:
            final = draft_with_persona(persona_id, topic)
            posts = final.get("posts") or []
            print("\n[final posts after disclosure]:")
            for p in posts:
                print(f"  - {p}")
            # Sanity check: disclosure must appear since is_real_person=True.
            joined = "\n".join(posts)
            assert "[AI persona of @abhi" in joined, "disclosure not enforced!"
            print("[ok] disclosure enforced")
        return 0
    finally:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
