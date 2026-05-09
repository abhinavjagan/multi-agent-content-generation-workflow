# x-agent

A LangGraph agent that drafts a short blog/article on a topic using a **local Ollama** LLM, formats it for **X (Twitter)** as a single tweet or numbered thread, asks you to **approve, edit, or regenerate**, and then posts it to your X account via the X API v2.

```
topic --> generate (Ollama) --> format (single|thread) --> human review --> post to X
```

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com/) running locally with at least one model pulled (default: `llama3:latest`).
  ```bash
  ollama pull llama3:latest
  ollama serve   # usually started automatically by the desktop app
  ```
  Any tag from `ollama list` works; override with `OLLAMA_MODEL=...` in `.env` or `--model` on `x-agent draft`. Every command that calls Ollama runs a fast pre-flight and exits with a clear message if the configured tag isn't pulled.
- An X developer app with **OAuth 1.0a User Context** tokens (read + write). See [docs.x.com/x-api](https://docs.x.com/x-api/introduction).

## Install

```bash
git clone <this repo>
cd x-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# edit .env and fill in your X API tokens
```

## Usage

### CLI

Dry-run (no posting, just print what would be sent):

```bash
x-agent draft "Why local LLMs matter in 2026" --mode thread --dry-run
```

Real run with HITL approval:

```bash
x-agent draft "Why local LLMs matter in 2026" --mode thread
```

Options:
- `--mode {single,thread}` — single tweet (<=275 chars) or numbered thread.
- `--style "punchy and technical"` — passed to the writer prompt.
- `--model llama3:latest` — override `OLLAMA_MODEL` (must be a tag listed by `ollama list`).
- `--persona <id>` — write in a saved persona's voice (see "Persona clone" below).
- `--dry-run` — never call the X API.
- `--research` / `--no-research` — ground the draft in fetched web pages (off by default; see "Web research" below).
- `--url <URL>` — repeatable, max 5; takes precedence over search. Implies `--research`.
- `--query <STR>` — override the search query (defaults to TOPIC). Implies `--research`.

The CLI will pause and show the draft, then prompt:

```
[a]pprove   [e]dit   [r]egenerate   [q]uit
```

Other top-level commands:
- `x-agent version` — print the package version.
- `x-agent persona ...` — manage cloned personas (see "Persona clone" below).

### Programmatic / server

A FastAPI app lives at `src/x_agent/server.py`. All routes are under `/api`
(plus a legacy `/healthz` for liveness probes). Run with:

```bash
uvicorn x_agent.server:app --reload --host 127.0.0.1
```

Useful endpoints:

- `GET  /api/health` — Ollama reachability + pulled tags, X-creds presence (boolean), persona count, version. **Never** returns secret values.
- `POST /api/draft` + `POST /api/approve/{thread_id}` — draft and HITL review loop.
- `GET/POST/DELETE /api/personas` (+ `/personas/{id}/answer`, `/refine`, `/refine/questions`, `/transcript`, `/resume-extract`) — persona lifecycle.
- `POST /api/personas/{id}/eval` — Server-Sent Events stream (`event: score` per row, `event: done` with the average) used by the Web UI.

> Security note: there is no authentication. Bind to `127.0.0.1` and put an
> authenticated reverse proxy in front before exposing it. The X tokens that
> back `/api/draft` are highly sensitive.

### Web UI

There is a polished React + Vite + TypeScript SPA under `frontend/` that
covers every flow (draft + HITL review, persona list / interview / refine /
eval, settings + health dashboard).

Dev mode runs the API on `:8000` and the Vite dev server on `:5173`. Vite
proxies `/api` to the API, so it's same-origin from the browser's POV.

```bash
# one-shot: start both with hot-reload (Ctrl-C kills both)
./scripts/dev.sh

# or, in two terminals
uvicorn x_agent.server:app --reload --host 127.0.0.1
cd frontend && npm install && npm run dev
```

For a single-process production deploy, build the SPA once and FastAPI will
mount it at `/` via `StaticFiles`:

```bash
cd frontend && npm install && npm run build
uvicorn x_agent.server:app --host 127.0.0.1
# -> open http://127.0.0.1:8000
```

## Architecture

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐
│  topic   │->│  load    │->│ research │->│ generate │->│  review  │->│  post  │
│  +urls?  │  │ persona  │  │ (opt-in) │  │ (Ollama) │  │ interrupt│  │ tweepy │
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └────────┘
                                                             │  edit/regenerate
                                                             │   loops back
```

The `research` node is a no-op unless the caller sets `research_enabled` on
the request. When on, it either fetches user-supplied URLs or searches the
web via the configured provider and feeds extracted article text into the
generation prompt as a `WEB CONTEXT` block.

State is checkpointed via LangGraph's `MemorySaver`, so the `interrupt()` in the review node can be resumed by re-invoking the graph with `Command(resume=...)`.

## Web research

The drafting agent can ground a post in fetched web pages. It is off by default
on every command and request — by design, x-agent stays local-only until the
caller explicitly opts in. When enabled the agent does ONE of two things:

- **URL mode** (preferred): you pass one or more `--url`s (CLI) or fill the
  "URLs to summarize" textarea (UI). The agent fetches each, extracts article
  text via `trafilatura`, and uses that as grounding. Search is skipped.
- **Topic-driven search**: with no URLs provided, the agent searches the web
  for `--query` (or the topic) via the configured provider, then fetches the
  top-k results.

Pluggable providers, in order of precedence:

| Provider     | Required env var          | Notes |
| ---          | ---                       | --- |
| Tavily       | `TAVILY_API_KEY`          | AI-tuned snippets, free 1000/mo. |
| Brave Search | `BRAVE_SEARCH_API_KEY`    | Free 2000/mo at 1 qps. |
| DuckDuckGo   | (none)                    | Default fallback; no key needed. |

Set `RESEARCH_PROVIDER=auto` (the default) to let the agent pick the best one
given your env. CLI examples:

```bash
# search-only (DuckDuckGo by default)
x-agent draft "what makes a good engineering culture" --research

# fetch a specific URL the user already chose
x-agent draft "what's new in postgres 18" \
    --url https://www.postgresql.org/about/news/postgresql-18-released-3142/

# override the search query without changing the topic
x-agent draft "engineering culture" --query "founder mode vs manager mode"
```

The same fields are exposed on `POST /api/draft` and
`POST /api/draft/variants` (`research_enabled`, `research_urls`,
`research_query`). `POST /api/research/preview` returns just the source list
without invoking the LLM, so the UI can show the user what was found before
generation starts. Source cards are surfaced under both the review screen and
each variant card.

Hardening (matches the workspace `codeguard-*` rules):

- SSRF: scheme allow-list (`http`/`https` only); the resolved IP is checked
  against `is_private | is_loopback | is_link_local | is_reserved | is_multicast`
  **before** the request; redirects are disabled.
- DoS: 10 s per-request HTTP timeout (configurable), 1 MB per-response cap,
  ~12 s overall wall-clock cap, max 4 concurrent fetches.
- Privacy: API keys are stored as `SecretStr` and surfaced in `/api/health`
  only as booleans. The chosen provider name is shown so you can see who your
  query is going to.

## Security

- All secrets come from environment variables (`pydantic-settings`); nothing hardcoded.
- `.env` is gitignored. Only `.env.example` is committed.
- The X client uses HTTPS via `tweepy`. We never log access tokens or full secrets.
- Topic input is length-bounded and stripped of control characters before being sent to the LLM or X.
- The server itself is local-only (Ollama on localhost, persona store on local disk). **Web research is the only feature that issues outbound HTTP** — and only when the caller opts in. With it enabled, your query and/or URL list goes to the configured search provider and the target page bodies are fetched directly from this process.

## Testing

```bash
pytest
```

### End-to-end smoke tests

These hit the real local Ollama server and exercise the full graph in dry-run mode (no X API call, no credentials needed):

```bash
# generate, format, auto-approve, fake-post
python scripts/smoke.py "How to debug a memory leak in a long-running Python service" thread

# exercise the human-in-the-loop EDIT pathway
python scripts/smoke_edit.py

# exercise the full persona path: canned interview -> spec -> draft -> critic -> dry-run post
python scripts/smoke_persona.py
```

Override the model with `OLLAMA_MODEL=llama3.2:latest python scripts/smoke.py ...`.

## Persona clone (write in someone's voice)

The agent can be conditioned on a persona captured by a structured interview. There is no scraping or data import: the subject talks to the agent, and that conversation becomes both the example bank and the source for an extracted persona spec.

```
interview (Phase A)                   draft (Phase B)
  ask question                          load persona
  -> answer (interrupt)                 -> retrieve_examples (top-k)
  -> judge_followup                     -> generate_draft (persona-conditioned)
  -> repeat                             -> format_for_x
  -> extract spec (LLM)                 -> persona_critic (loops if score low)
  -> embed transcript                   -> human_review
  -> save                               -> post_to_x (auto-disclosure)
```

### Prereqs

```bash
ollama pull llama3:latest         # or your chat model of choice; matches OLLAMA_MODEL default
ollama pull nomic-embed-text      # used for transcript retrieval
```

### CLI

```bash
# 1) Run the interview. For real people, the CLI captures consent + a disclosure tag.
#    Add --quick to do it in 6 questions instead of ~17 (great for a first pass).
x-agent persona create --name "Abhi" --handle abhi --quick
# answer each question; finish each answer with a single line containing "."
# use "/skip" to skip a question

# 2) List / show / delete saved personas
x-agent persona list
x-agent persona show abhi-XXXXXXXX
x-agent persona delete abhi-XXXXXXXX

# 3) Draft a post AS the persona (HITL approval still required)
x-agent draft "what makes a good distributed-tracing span name" \
    --persona abhi-XXXXXXXX --mode single --dry-run

# 4) Add more interview answers later to refine
x-agent persona refine abhi-XXXXXXXX --dimension humor

# 5) Sanity-check the persona on a fixed topic battery
x-agent persona eval abhi-XXXXXXXX

# 6) Recover from a crashed extraction (LLM hiccup, wrong model, etc.).
#    The transcript is persisted before extraction, so this re-runs the
#    LLM extractor + embeddings against the saved Q+A without redoing
#    the interview.
x-agent persona resume-extract abhi-XXXXXXXX
```

### What gets stored, where

```
~/.x-agent/personas/<persona-id>/
    spec.json          # PersonaSpec (voice, values, opinions, banned/signature phrases, ...)
    transcript.jsonl   # the raw Q+A interview (one entry per line)
    embeddings.npz     # nomic-embed-text vectors over each Q+A
```

Files have `0600` permissions and the directory `0700`. `.env` and persona files are never logged.

### Safety / disclosure

- `--real` (the default for `persona create`) requires both **consent acknowledgement** and a **disclosure tag** (e.g. `[AI persona of @abhi]`). The tag is auto-injected on every generated post; if it pushes a tweet over the X limit, the disclosure goes out as its own tweet at the end of the thread.
- `--fictional` skips both, for synthetic personas.

### Persona consistency critic

After every draft, an LLM judge scores the post 0-5 against the persona spec + retrieved transcript chunks. If the score is below `CRITIC_MIN_SCORE` (default 4), the graph loops back to `generate_draft` with the violation list and a suggestion in the prompt, up to `CRITIC_MAX_ATTEMPTS` (default 2). Then the human still reviews.

### HTTP API

The same FastAPI app exposes:
- `POST /personas` -> start an interview (returns `thread_id` and the first question)
- `POST /personas/{thread_id}/answer` -> submit the next answer (returns the next question or `saved: true`)
- `GET /personas`, `GET /personas/{id}`, `DELETE /personas/{id}`
- `POST /draft` now accepts an optional `persona_id`

## Limitations / future work

- Single-account only, OAuth 1.0a user-context tokens read from `.env`.
- In-memory checkpointer; swap to `SqliteSaver` for durable HITL pauses across restarts.
- No image / media attachments yet.
- No scheduling.
- Persona retrieval uses an in-process numpy cosine; fine for one transcript, not for thousands of personas.
- No fine-tuning yet; relies entirely on prompt + retrieval + critic.
