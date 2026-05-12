"""LangGraph nodes for the x-agent draft -> review flow.

When a ``persona_id`` is set in state, the graph routes through:
``load_persona -> retrieve_examples -> generate_draft -> format_for_x ->
persona_critic -> human_review``.
Without a persona, ``load_persona`` and ``retrieve_examples`` are no-ops
and ``persona_critic`` short-circuits, preserving the original flow.

x-agent never publishes anywhere. ``human_review`` ends the graph after
an ``approve`` action and the caller hands the finalized posts to the
user to copy / open in X compose themselves.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.types import Command, interrupt

from .config import get_settings
from .formatter import sanitize_topic, split_into_thread, trim_to_single
from .persona.critic import score_against_persona
from .persona.embedder import PersonaEmbedder
from .persona.markdown import summarize_for_prompt
from .persona.schema import PersonaSpec
from .persona.store import PersonaNotFoundError, get_default_store
from .research import WebResult, gather_research
from .state import AgentState

log = logging.getLogger(__name__)


_WRITER_SYSTEM_PROMPT = (
    "You are a senior engineer writing a short blog post for X (Twitter).\n"
    "\n"
    "Voice rules (strict):\n"
    "- Plain English, short declarative sentences. Engineer's notebook style.\n"
    "- Be opinionated and specific. Name actual tools, libraries, commands, file paths,\n"
    "  metrics, or numbers wherever relevant.\n"
    "- No hashtags. No emojis. No 'Here is...' / 'In this post...' meta phrasing.\n"
    "- Banned opening phrases: 'In a world where', 'In today's fast-paced',\n"
    "  'It is important to note', 'As we all know', 'Have you ever'.\n"
    "- Banned filler words: leverage, unlock, ecosystem, paradigm, journey,\n"
    "  game-changer, cutting-edge, revolutionary.\n"
    "- Do NOT include a title, headings, bullet lists, numbering, or markdown.\n"
    "  Plain paragraphs separated by a single blank line.\n"
    "- Output ONLY the post body. No preamble, no closing summary, no 'Hope this helps'.\n"
)


_PERSONA_WRITER_SYSTEM_PROMPT = (
    "You write a short post for X (Twitter) IN THE EXACT VOICE of a specific\n"
    "person, captured from an interview. Defer to the PERSONA + EXAMPLES blocks\n"
    "in the user message - they override every default style instinct you have.\n"
    "\n"
    "Hard rules:\n"
    "- Match the EXAMPLES exactly for: capitalization (esp. lowercase-only),\n"
    "  punctuation (incl. missing apostrophes / commas / triple !!!), slang,\n"
    "  abbreviations (rn, tbh, ngl, lol, imo, bc, u, ur, w/), sentence rhythm,\n"
    "  and energy level. If the examples are lowercase, you write lowercase.\n"
    "  If the examples drop punctuation, you drop punctuation.\n"
    "- Never use the persona's banned phrases. Never sound like LinkedIn or a\n"
    "  press release.\n"
    "- No hashtags. No emojis. No markdown, no headings, no bullet lists.\n"
    "- No meta preamble ('Here is...', 'In this post...'). Open with a\n"
    "  concrete claim, observation, or hot take.\n"
    "- Stay on-topic. Keep within the requested length.\n"
    "- Output ONLY the post body. Nothing else.\n"
)


def _build_llm(state: AgentState, *, temperature: float = 0.7) -> ChatOllama:
    settings = get_settings()
    model = state.get("model") or settings.ollama_model
    # Caller-supplied temperature in state wins; the variants endpoint sets
    # this so it can fan out N parallel generations at diverse temps.
    override = state.get("temperature")
    effective = float(override) if override is not None else temperature
    # Clamp into a sane range so a bad caller value can't break sampling.
    effective = max(0.0, min(2.0, effective))
    return ChatOllama(
        base_url=settings.ollama_base_url,
        model=model,
        temperature=effective,
    )


# ----------------------------------------------------------------- persona nodes


def load_persona(state: AgentState) -> dict[str, Any]:
    """Resolve ``persona_id`` to a PersonaSpec dict (no-op if not set)."""
    persona_id = state.get("persona_id")
    if not persona_id:
        return {}
    try:
        spec = get_default_store().load(persona_id)
    except PersonaNotFoundError:
        return {"error": f"persona not found: {persona_id}"}
    log.info("load_persona id=%s name_present=%s", persona_id, bool(spec.name))
    return {"persona": spec.model_dump(mode="json")}


def retrieve_examples(state: AgentState) -> dict[str, Any]:
    """Pull top-k example chunks for the topic from the persona's index."""
    persona_id = state.get("persona_id")
    if not persona_id:
        return {"retrieved_examples": []}
    store = get_default_store()
    embeddings = store.load_embeddings(persona_id)
    if embeddings is None:
        log.info("retrieve_examples id=%s no embeddings on disk", persona_id)
        return {"retrieved_examples": []}
    ids, vectors = embeddings
    transcript = store.read_transcript(persona_id)
    text_by_id: dict[str, str] = {}
    for entry in transcript:
        cid = f"{entry.dimension}:{int(entry.timestamp.timestamp())}"
        text_by_id[cid] = f"Q: {entry.question}\nA: {entry.answer}"
    texts = [text_by_id.get(i, "") for i in ids]

    settings = get_settings()
    embedder = PersonaEmbedder()
    try:
        results = embedder.retrieve(
            query=state.get("topic", ""),
            ids=ids,
            texts=texts,
            vectors=vectors,
            k=settings.persona_top_k,
        )
    except Exception as exc:  # noqa: BLE001 - retrieval is best-effort
        log.warning("retrieve_examples failed: %s", exc)
        return {"retrieved_examples": []}
    return {"retrieved_examples": [r.text for r in results if r.text]}


# --------------------------------------------------------------- research node


def research(state: AgentState) -> dict[str, Any]:
    """Optional pre-draft step: fetch URLs / search the web for context.

    No-op when ``research_enabled`` is False or when the call returns no
    results -- the rest of the graph treats an empty ``web_results`` as
    "no extra grounding", same as before this feature existed.

    State is JSON-serialised by LangGraph's checkpointer, so we store
    ``WebResult.model_dump(mode='json')`` (string URLs) rather than the
    Pydantic objects directly.
    """
    if not state.get("research_enabled"):
        return {"web_results": []}

    settings = get_settings()
    urls = state.get("research_urls") or []
    query = state.get("research_query") or state.get("topic", "")
    try:
        results = gather_research(
            query=query,
            urls=urls,
            k=settings.research_max_results,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 - never break the graph
        log.warning("research node crashed (%s): %s", type(exc).__name__, exc)
        return {"web_results": []}

    log.info(
        "research node returned %d result(s) urls=%d query_len=%d",
        len(results), len(urls), len(query),
    )
    return {"web_results": [r.model_dump(mode="json") for r in results]}


def _web_context_block(web_results: list[dict] | None) -> str:
    """Render fetched/searched sources as a prompt block.

    Format intentionally compact (numbered, with hostname) so the LLM can
    cite ``[1]`` / ``[2]`` if it wants to without bloating the context
    window. We feed the SHORT form of the content (snippet OR truncated
    article body) -- the full body lives in the API response for the UI.
    """
    if not web_results:
        return ""
    lines: list[str] = []
    for i, raw in enumerate(web_results[:6], start=1):
        try:
            r = WebResult.model_validate(raw)
        except Exception:  # noqa: BLE001
            continue
        host = r.host()
        title = (r.title or host or "source").strip()[:160]
        body = r.short(360)
        if not body:
            continue
        lines.append(f"[{i}] {title} ({host})\n{body}")
    if not lines:
        return ""
    return (
        "\nWEB CONTEXT (do not copy verbatim; you can reference numbers/"
        "names from these sources, but write in your own voice):\n"
        + "\n\n".join(lines)
    )


# -------------------------------------------------------------- draft generation


_SLANG_TOKENS = {
    "rn", "tbh", "ngl", "lol", "lmao", "lessgoo", "lessgo", "imo", "imho",
    "bc", "btw", "fr", "ya", "yea", "yep", "nope", "nah", "nahh", "lowkey",
    "highkey", "yeet", "vibe", "vibes", "bruh", "ur", "u", "wanna", "wannna",
    "gonna", "kinda", "sorta", "dont", "doesnt", "wasnt", "isnt", "wont",
    "thats", "id", "im", "ive", "yall", "wfh", "wdym", "smh", "jk", "fwiw",
    "afaik", "asap", "omg", "wtf", "ftfy", "tldr", "tl;dr",
}


def _voice_signals_from_examples(examples: list[str]) -> dict[str, Any]:
    """Cheap heuristic over interview examples to surface explicit style hints.

    Returns a dict with: ``mostly_lowercase``, ``drops_punctuation``,
    ``slang_used``. Cheap to compute; gives the writer concrete handles
    instead of relying on the LLM to infer style from raw text alone.
    """
    if not examples:
        return {"mostly_lowercase": False, "drops_punctuation": False, "slang_used": []}
    full = " ".join(examples)
    letters = [c for c in full if c.isalpha()]
    upper_ratio = (
        sum(1 for c in letters if c.isupper()) / max(1, len(letters))
    )
    sentence_count = max(1, sum(1 for c in full if c in ".!?"))
    word_count = max(1, len(full.split()))
    drops_punctuation = (word_count / sentence_count) > 30
    tokens = {t.strip(".,!?:;\"'()[]").lower() for t in full.split()}
    slang_seen = sorted(tokens & _SLANG_TOKENS)[:10]
    # "All lowercase" means essentially no uppercase at all - normal English
    # with proper nouns still hits ~5-7% upper, so use a tight threshold.
    return {
        "mostly_lowercase": upper_ratio < 0.02,
        "drops_punctuation": drops_punctuation,
        "slang_used": slang_seen,
    }


def _persona_block(persona: dict | None, examples: list[str] | None = None) -> str:
    """Render the writer-prompt block for ``persona``.

    The primary content is the long-form ``personality.md`` profile,
    which is a sectioned, transcript-quote-backed description of the
    human. We also append a short ``MECHANICAL STYLE`` block derived from
    the user's interview examples (lowercase / dropped punctuation /
    in-group slang) and a couple of hard guardrails (banned phrases,
    avoided topics) so the LLM has them in front of it even if the
    markdown gets truncated for prompt budget.
    """
    if not persona:
        return ""
    p = PersonaSpec.model_validate(persona)
    lines: list[str] = [f"You are writing AS {p.name}."]
    md = summarize_for_prompt(p.personality_md or "")
    if md:
        lines.append("")
        lines.append("PERSONALITY PROFILE (your source of truth):")
        lines.append(md)
        lines.append("")

    signals = _voice_signals_from_examples(examples or [])
    style_hints: list[str] = []
    if signals["mostly_lowercase"]:
        style_hints.append("write in all lowercase (no capital letters)")
    if signals["drops_punctuation"]:
        style_hints.append(
            "drop most punctuation; let sentences run together with a comma or dash"
        )
    if signals["slang_used"]:
        style_hints.append(
            "reuse slang/abbreviations naturally where they fit: "
            + ", ".join(signals["slang_used"])
        )
    if style_hints:
        lines.append("MECHANICAL STYLE (from real samples): " + "; ".join(style_hints) + ".")
    if p.banned_phrases:
        lines.append(
            "NEVER use these phrases: " + ", ".join(p.banned_phrases[:12])
        )
    if p.topics_avoided:
        lines.append("Avoid these topics: " + ", ".join(p.topics_avoided[:6]))
    return "\n".join(lines)


def generate_draft(state: AgentState) -> dict[str, Any]:
    """Ask Ollama to write a draft, persona-conditioned if a persona is loaded.

    When ``state["seed_posts"]`` is non-empty AND no critic retry has been
    requested yet, we skip the LLM entirely and treat the joined seed text
    as the draft. This is how the "pick a variant" flow re-enters the normal
    HITL pipeline with the variant the user chose: no second (different)
    generation, no extra LLM cost. Subsequent retries (from the critic loop
    or a Regenerate action) clear the seed so the user can iterate normally.
    """
    topic = sanitize_topic(state["topic"])
    style = state.get("style") or "punchy, technical, plain prose"
    mode = state.get("mode", "thread")
    persona = state.get("persona")
    examples = state.get("retrieved_examples") or []
    critic_violations = state.get("critic_violations") or []
    critic_suggestion = state.get("critic_suggestion") or ""

    seed_posts = state.get("seed_posts") or []
    attempts = int(state.get("critic_attempts", 0))
    if seed_posts and attempts == 0:
        log.info("generate_draft seeded posts=%d (LLM skipped)", len(seed_posts))
        # Drop the seed after using it so a later regenerate/critic-retry
        # actually calls the LLM.
        return {
            "draft": "\n\n".join(p for p in seed_posts if p),
            "topic": topic,
            "seed_posts": None,
        }

    if mode == "single":
        target = (
            "EXACTLY one paragraph, 40-60 words, that fits in a single tweet "
            "(<= 260 characters total). Make every word earn its place."
        )
    else:
        target = (
            "200-300 words total, organized as 4-6 short paragraphs separated "
            "by a single blank line. Each paragraph should be one tight idea "
            "(roughly 2-4 sentences) that stands on its own as a tweet."
        )

    user_lines = [
        f"Topic: {topic}",
        f"Style hint: {style}",
        f"Length & structure: {target}",
    ]

    persona_block = _persona_block(persona, examples)
    if persona_block:
        user_lines.append("\nPERSONA:\n" + persona_block)
    if examples:
        ex_block = "\n\n".join(
            f"EXAMPLE {i + 1}:\n{ex}" for i, ex in enumerate(examples[:6])
        )
        user_lines.append(
            "\nThese are real things this person said in their interview. "
            "Match this voice closely - mirror the casing, punctuation, slang, "
            "and rhythm:\n" + ex_block
        )
    if critic_violations or critic_suggestion:
        user_lines.append(
            "\nA previous draft was rejected by the consistency critic. "
            f"Violations: {'; '.join(critic_violations) or '(none listed)'}.\n"
            f"Suggestion: {critic_suggestion or '(none)'}"
        )

    web_block = _web_context_block(state.get("web_results"))
    if web_block:
        user_lines.append(web_block)

    user_lines.append(
        "\nOpen with a concrete claim or observation - no setup, no preamble. "
        "If the topic is ambiguous, pick the most technically interesting reading. "
        "Write the post now."
    )

    system_prompt = (
        _PERSONA_WRITER_SYSTEM_PROMPT if persona else _WRITER_SYSTEM_PROMPT
    )

    # When personas drive a casual / playful voice, raise temperature a bit.
    temperature = 0.7
    if persona:
        try:
            spec = PersonaSpec.model_validate(persona)
            if spec.voice.formality <= 2 or spec.voice.humor in {"sarcastic", "warm"}:
                temperature = 0.9
        except Exception:  # noqa: BLE001 - defensive
            pass

    llm = _build_llm(state, temperature=temperature)
    log.info(
        "generate_draft topic_len=%d mode=%s persona=%s examples=%d web=%d retry=%d temp=%.1f",
        len(topic),
        mode,
        bool(persona),
        len(examples),
        len(state.get("web_results") or []),
        state.get("critic_attempts", 0),
        temperature,
    )
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n".join(user_lines)),
    ])
    draft = (response.content or "").strip()
    return {"draft": draft, "topic": topic}


def format_for_x(state: AgentState) -> dict[str, Any]:
    """Convert the draft into a list of tweet-sized posts."""
    settings = get_settings()
    mode = state.get("mode", "thread")
    draft = state.get("draft", "")
    if not draft:
        return {"posts": [], "error": "format_for_x called with empty draft"}

    if mode == "single":
        posts = [trim_to_single(draft, settings.max_tweet_chars)]
    else:
        posts = split_into_thread(draft, settings.max_tweet_chars)

    log.info("format_for_x mode=%s tweets=%d", mode, len(posts))
    return {"posts": posts}


# --------------------------------------------------------- variant generation
#
# A non-graph helper used by ``POST /api/draft/variants``. It runs exactly the
# same nodes as the HITL graph (``load_persona`` -> ``retrieve_examples`` ->
# ``generate_draft`` -> ``format_for_x``) plus a direct call to the persona
# critic, then returns. There's no checkpointer, no interrupt, no human
# review - the endpoint fans this out N times in parallel at diverse
# temperatures so the user can pick the variant they like best, which then
# enters the normal HITL flow via ``POST /api/draft`` with ``seed_posts``.


def run_variant(state: AgentState, *, score: bool = False) -> dict[str, Any]:
    """Synchronously produce one variant: posts (+ optional critic score).

    The result dict has the same shape callers expect from the HITL graph at
    the human_review interrupt: ``posts``, ``critic_score``,
    ``critic_violations``, ``critic_suggestion``. Plus ``temperature``
    (echoed from input state) and ``error`` (None on success).

    ``score=False`` (default) skips the critic LLM call. The variants
    endpoint defaults to off because the critic doubles wall-clock latency
    and Ollama serialises concurrent requests unless ``OLLAMA_NUM_PARALLEL``
    is set; the post-pick HITL flow always runs the critic, so the user
    still gets a score for the variant they actually chose.
    """
    cur: dict[str, Any] = dict(state)
    cur.update(load_persona(cur))
    if cur.get("error"):
        return {
            "posts": [],
            "critic_score": None,
            "critic_violations": [],
            "critic_suggestion": "",
            "temperature": cur.get("temperature"),
            "error": cur["error"],
        }
    cur.update(retrieve_examples(cur))
    # Research: if the caller already populated ``web_results`` (variants
    # endpoint pre-fetches once and reuses across N variants), skip the
    # network call. Otherwise honour ``research_enabled`` and run it.
    if not cur.get("web_results") and cur.get("research_enabled"):
        cur.update(research(cur))
    cur.update(generate_draft(cur))
    cur.update(format_for_x(cur))

    posts: list[str] = list(cur.get("posts") or [])
    critic_score: int | None = None
    critic_violations: list[str] = []
    critic_suggestion: str = ""

    persona = cur.get("persona")
    if score and persona and posts:
        spec = PersonaSpec.model_validate(persona)
        examples = cur.get("retrieved_examples") or []
        result = score_against_persona(
            draft="\n\n".join(posts), persona=spec, examples=examples,
        )
        critic_score = int(result.get("score", 5))
        critic_violations = list(result.get("violations") or [])
        critic_suggestion = str(result.get("suggestion") or "")

    return {
        "posts": posts,
        "critic_score": critic_score,
        "critic_violations": critic_violations,
        "critic_suggestion": critic_suggestion,
        "temperature": cur.get("temperature"),
        "error": cur.get("error"),
    }


# ------------------------------------------------------------------ critic node


def persona_critic(state: AgentState) -> Command:
    """Score the draft against the persona; loop back to generation on failure."""
    persona = state.get("persona")
    if not persona:
        return Command(goto="human_review")

    settings = get_settings()
    attempts = int(state.get("critic_attempts", 0))
    posts = state.get("posts") or []
    draft_text = "\n\n".join(posts) if posts else state.get("draft", "")

    spec = PersonaSpec.model_validate(persona)
    examples = state.get("retrieved_examples") or []
    result = score_against_persona(draft=draft_text, persona=spec, examples=examples)

    score = int(result.get("score", 5))
    violations = list(result.get("violations") or [])
    suggestion = str(result.get("suggestion") or "")
    log.info(
        "persona_critic score=%d violations=%d attempts=%d/%d",
        score, len(violations), attempts, settings.critic_max_attempts,
    )

    if score >= settings.critic_min_score or attempts >= settings.critic_max_attempts:
        return Command(
            update={
                "critic_score": score,
                "critic_violations": violations,
                "critic_suggestion": suggestion,
            },
            goto="human_review",
        )

    return Command(
        update={
            "critic_score": score,
            "critic_violations": violations,
            "critic_suggestion": suggestion,
            "critic_attempts": attempts + 1,
        },
        goto="generate_draft",
    )


# --------------------------------------------------------------------- review


def human_review(state: AgentState) -> Command:
    """Pause the graph and surface the draft to a human reviewer.

    Approving simply records ``finalized=True`` and ends the graph; the
    caller (CLI / API / SPA) presents the finalized posts to the user to
    copy or hand off to X compose. We also enforce the persona disclosure
    tag on the first tweet here, so an edit pass that removed it still
    ships a compliant artifact.
    """
    posts: list[str] = list(state.get("posts", []))
    decision = interrupt({
        "kind": "review",
        "topic": state.get("topic", ""),
        "mode": state.get("mode", "thread"),
        "posts": posts,
        "persona_id": state.get("persona_id"),
        "critic_score": state.get("critic_score"),
        "critic_violations": state.get("critic_violations") or [],
    })

    action = (decision or {}).get("action", "approve").lower()

    if action == "approve":
        return Command(
            update={"finalized": True, "posts": _enforce_disclosure(posts, state)},
            goto="__end__",
        )
    if action == "reject":
        return Command(update={"rejected": True}, goto="__end__")
    if action == "edit":
        edited = (decision or {}).get("edited") or "\n\n".join(posts)
        return Command(update={"draft": edited}, goto="format_for_x")
    if action == "regenerate":
        return Command(update={"critic_attempts": 0}, goto="generate_draft")
    log.warning("human_review unknown action %r, treating as reject", action)
    return Command(update={"rejected": True}, goto="__end__")


def _enforce_disclosure(posts: list[str], state: AgentState) -> list[str]:
    """Append/split the persona disclosure tag on the first tweet if needed."""
    if not posts:
        return posts
    persona = state.get("persona")
    if not persona:
        return posts
    try:
        spec = PersonaSpec.model_validate(persona)
    except Exception:  # noqa: BLE001
        return posts
    if not spec.requires_disclosure():
        return posts
    out = list(posts)
    out[0] = spec.ensure_disclosed(out[0])
    settings = get_settings()
    if len(out[0]) > settings.max_tweet_chars:
        tag = spec.disclosure_text.strip()
        out[0] = out[0].replace("\n\n" + tag, "").rstrip()
        out.append(tag)
        log.info("human_review disclosure split into separate tweet")
    return out
