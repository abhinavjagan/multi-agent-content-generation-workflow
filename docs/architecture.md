# Architecture

x-agent is two LangGraph state machines plus a FastAPI server that bundles a React SPA.

## Draft graph

```
topic + persona ──> load_persona ──> retrieve_examples ──> research? ──> generate_draft ──> format_for_x ──> persona_critic ──> human_review ──> END
                                                                                                   ↑                              │
                                                                                                   └── score < threshold ─────────┘
                                                                                                                                  │
                                                                                                            action=regen ─────────┘
                                                                                                            action=edit  ─── apply edits → END
```

- `load_persona` reads `spec.json` + `personality.md` from `~/.x-agent/personas/<id>/`.
- `retrieve_examples` does top-k cosine over the in-memory embedding index of that persona's transcript.
- `research` (opt-in) either fetches user-supplied URLs or hits the configured search provider, then summarizes the article text into a `WEB CONTEXT` block.
- `generate_draft` runs Ollama with a system prompt built from `personality.md` plus a `MECHANICAL STYLE` block and hard guardrails.
- `format_for_x` chunks the output into a single tweet or numbered thread (≤ `MAX_TWEET_CHARS`).
- `persona_critic` scores 0-5 against the persona; below `CRITIC_MIN_SCORE` it loops up to `CRITIC_MAX_ATTEMPTS`.
- `human_review` uses LangGraph's `interrupt()` to pause for the user's approve/edit/regenerate decision. On approve the disclosure tag is enforced for real-person personas. Then the graph ends with `posts: list[str]` in state — the UI/CLI hands these to the user. **There is no `post_to_x` node.**

State lives in LangGraph's `MemorySaver`, which means a server restart drops in-flight reviews. Personas, transcripts, and the recent-drafts log are durable on disk.

## Interview graph (Phase A)

```
START ──> ask_next_question ──[interrupt]──> record_answer ──> judge_followup ──┬─> ask_next_question
                                                                                ├─> extract (LLM)
                                                                                └─> embed_and_save ──> END
```

- Each answer is **appended progressively** to `transcript.jsonl` on disk *before* the LLM extractor runs, so a crash never loses interview content.
- `extract` builds the structured `PersonaSpec` and the long-form `personality.md` via the deterministic renderer in `src/x_agent/persona/markdown.py`.
- `embed_and_save` writes the transcript embeddings via `nomic-embed-text` and persists everything.

## Runtime layout

```
src/x_agent/
  server.py          FastAPI app; mounts frontend/dist at /
  cli.py             Typer CLI; `x-agent draft|generate|persona ...`
  graph.py           build_draft_graph()
  interview_graph.py build_interview_graph()
  nodes.py           every node + the persona block builder
  state.py           AgentState TypedDict
  config.py          pydantic-settings (loaded from env / .env)
  research/          provider abstraction (Tavily/Brave/DuckDuckGo) + fetcher
  persona/
    schema.py        PersonaSpec, TranscriptEntry
    interview.py     LLM extractor → PersonaSpec
    markdown.py      render_personality_md, summarize_for_prompt
    store.py         filesystem store (specs, transcripts, embeddings, md)
    embedder.py      nomic-embed-text wrapper
    critic.py        persona consistency judge
    questions.py     question bank + quick mode
frontend/
  src/pages          Dashboard, Draft, Personas, PersonaCreate, PersonaDetail, Settings, NotFound
  src/components     UI primitives (glass surfaces), decor (CoolShape), layout
  src/lib            api client, types, recentDrafts persistence
```

## Native runtime (no Docker)

You don't *need* Docker. The Quickstart uses it for one-command setup, but every flow works natively:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cd frontend && npm install && npm run build && cd ..

cp .env.example .env
# defaults work; edit if your Ollama lives elsewhere

uvicorn x_agent.server:app --host 127.0.0.1
# → open http://127.0.0.1:8000
```

For hot-reload while developing the SPA, run the API and the Vite dev server in parallel:

```bash
./scripts/dev.sh
# or, in two terminals:
uvicorn x_agent.server:app --reload --host 127.0.0.1
cd frontend && npm run dev   # proxied /api → :8000
```

## Why no posting?

This was a deliberate scope cut. The earlier design wrote to X via `tweepy` with OAuth 1.0a user-context tokens, which forced every user through Twitter Developer Portal approval and put production-grade credentials on disk. Removing it shrinks the trust footprint, drops the `tweepy` dependency, and keeps the entire tool on `localhost` with no outbound writes. After approval the UI hands you a copy block and an `https://twitter.com/intent/tweet?text=…` deep link — you publish from your own account, in your own browser.
