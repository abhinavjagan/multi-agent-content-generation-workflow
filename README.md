# x-agent

A LangGraph agent that drafts a short blog/article on a topic using a **local Ollama** LLM, formats it for **X (Twitter)** as a single tweet or numbered thread, asks you to **approve, edit, or regenerate**, and then posts it to your X account via the X API v2.

```
topic --> generate (Ollama) --> format (single|thread) --> human review --> post to X
```

> Want to skip installing Python and Node on your host? See [Quickstart with Docker](#quickstart-with-docker) below — one command runs the FastAPI agent and the SPA in a container while talking to Ollama on your host.

## Requirements

> Going the Docker route? You only need **Ollama** (next bullet) and Docker — skip Python and Node, they aren't used on your host. Jump to [Quickstart with Docker](#quickstart-with-docker).

- **Python 3.10+** (3.11/3.12 also tested).
- **[Ollama](https://ollama.com/)** running locally with these models pulled:
  ```bash
  ollama pull llama3:latest         # generation + persona consistency critic
  ollama pull nomic-embed-text      # transcript retrieval (only needed for personas)
  ollama serve                      # usually started automatically by the desktop app
  ```
  Any chat model tag from `ollama list` works; override the default with `OLLAMA_MODEL=...` in `.env` or `--model` on `x-agent draft`. Every command runs a fast pre-flight and exits with a clear message if the configured tag isn't pulled.
- **Node.js 18+ and npm** — only if you want the web UI under `frontend/`. The CLI has no Node dependency.
- **An X developer app** with **OAuth 1.0a User Context** tokens (read + write) — *optional*. Without them, every command and request runs in `--dry-run` mode. See [docs.x.com/x-api](https://docs.x.com/x-api/introduction).

## Quickstart with Docker

If you'd rather not install Python and Node on your host, you can run the FastAPI agent and the bundled SPA inside a container. **Ollama still runs on the host** — that keeps Metal GPU acceleration available on Apple Silicon (Docker on macOS can't reach Metal) and avoids baking multi-GB model weights into the image. The same compose file works on Linux as long as Ollama is running on the host.

### Prereqs

```bash
# 1) Install and start Ollama on the host
brew install ollama          # or your distro's package manager
ollama serve &               # or just open the Ollama desktop app
ollama pull llama3:latest    # ~4.7 GB, generation + persona critic
ollama pull nomic-embed-text # ~274 MB, persona retrieval

# 2) Install Docker (Docker Desktop, Colima, or Docker Engine + the Compose plugin)
```

### Run the stack

```bash
git clone https://github.com/abhinavjagan/multi-agent-content-generation-workflow.git
cd multi-agent-content-generation-workflow
docker compose up --build -d        # -d runs detached so this terminal stays free
docker compose logs -f app          # optional: tail the app logs (Ctrl-C just stops the tail)
```

The first `up --build` takes ~2-5 minutes (one-time image build + Python deps); subsequent `docker compose up`s are instant. Drop `-d` if you'd rather watch the build attached — just remember the next CLI examples need a new terminal.

Verify the app is healthy:

```bash
curl -s http://localhost:8000/api/health | python -m json.tool
# → "ollama": { "ok": true, "available": ["llama3:latest", "nomic-embed-text"], ... }
```

If `ollama.ok` is `false`, the host-side Ollama daemon isn't running — start it with `ollama serve &` (or open the desktop app) and re-run the curl. Then open `http://localhost:8000` for the SPA. The app container reaches the host's Ollama daemon via `host.docker.internal:11434`; on Linux, compose adds `host-gateway` so the same hostname resolves to the host without any extra setup.

The CLI runs inside the running container:

```bash
docker compose exec app x-agent draft "Why local LLMs matter in 2026" --mode thread --dry-run
docker compose exec app x-agent persona create --name "Abhi" --handle abhi --quick
docker compose exec app pytest -q   # 142 tests, fully offline
```

### Optional: enable real X posting and paid research providers

Drop a `.env` next to `docker-compose.yml` (copy from `.env.example`) and fill in `X_API_KEY` / `TAVILY_API_KEY` / etc. The app picks it up automatically — no rebuild. Without `.env`, every command runs in dry-run and DuckDuckGo is the research provider, which is perfect for trying things out.

### Persistence and cleanup

| Volume | What's in it | Wiped by |
| --- | --- | --- |
| `persona_data` | persona spec + transcript + embeddings under `/data/personas` | `docker compose down -v` |

Model weights live in your host's Ollama install (`~/.ollama/models`) and are not managed by Docker — they survive container resets and are shared with any other Ollama-using tool on the host. `docker compose down` (without `-v`) keeps `persona_data`, so a later `docker compose up` picks up your personas; `docker compose down -v` wipes them.

### Container hardening

The app container runs as a non-root user (uid 10001), with read-only root filesystem (`tmpfs` for `/tmp`), all Linux capabilities dropped, and `no-new-privileges` enabled — see [Dockerfile](Dockerfile) and [docker-compose.yml](docker-compose.yml).

### Common ops

```bash
docker compose ps                    # service status + healthcheck state
docker compose logs -f app           # tail app logs
docker compose exec app sh           # shell into the app container
docker compose build --no-cache app  # force a clean rebuild
docker compose down                  # stop, keep persona volume
docker compose down -v               # stop AND wipe persona volume
```

> **Why isn't Ollama in a container too?** It can be, but it's a bad trade for this project. Docker on macOS can't pass through Metal, so containerized Ollama is CPU-only and ~10× slower for chat models. On Linux you'd need an NVIDIA GPU plus the NVIDIA Container Toolkit to break even. Most users running Ollama already have it on the host anyway. Keeping the daemon out of compose keeps the image small, inference fast, and the model cache shared with anything else on the host.

## Setup

After cloning, three steps get you to a working dev environment:

```bash
git clone https://github.com/abhinavjagan/multi-agent-content-generation-workflow.git
cd multi-agent-content-generation-workflow

# 1) Backend: virtualenv + editable install (creates .venv/ and src/x_agent.egg-info/)
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2) Frontend, only if you want the web UI (creates frontend/node_modules/)
cd frontend && npm install && cd ..

# 3) Configuration (creates .env from the template; never committed)
cp .env.example .env
$EDITOR .env
```

`.env.example` is the single source of truth for every knob. Skim it once; the four sections are:

- **Ollama** *(required)*: `OLLAMA_BASE_URL`, `OLLAMA_MODEL`. Defaults work if Ollama is on the same host.
- **X (Twitter) API** *(optional)*: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`. Leave blank to force every CLI/API call into dry-run.
- **Persona clone** *(optional)*: `PERSONA_DIR` (default `~/.x-agent/personas`), `EMBEDDING_MODEL`, critic + retrieval tunables.
- **Web research** *(optional)*: `RESEARCH_PROVIDER`, `TAVILY_API_KEY`, `BRAVE_SEARCH_API_KEY`, fetch limits. Without keys, DuckDuckGo is used automatically — no signup required.

### Smoke test the setup

```bash
# backend tests (fully offline, no Ollama needed)
pytest -q

# end-to-end with real Ollama in dry-run (no X creds needed)
python scripts/smoke.py "test topic" thread

# frontend type-check + production bundle (only if you ran the frontend step)
cd frontend && npm run typecheck && npm run build && cd ..
```

### What gets created on disk (and why it's gitignored)

Everything below is recreated automatically by the steps above or by normal use, which is exactly why none of it lives in version control.

| Path | Created by | Required for | Safe to delete? |
| --- | --- | --- | --- |
| `.env` | you (`cp .env.example .env`) | every command that reads config | NO — contains your secrets |
| `.venv/` | `python -m venv .venv` | running anything | yes (rerun setup step 1) |
| `src/x_agent.egg-info/` | `pip install -e .` | editable install metadata | yes (auto-recreated) |
| `__pycache__/`, `*.pyc` | Python at import time | nothing user-facing | yes |
| `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` | the named tool | nothing user-facing | yes |
| `frontend/node_modules/` | `npm install` | dev server + production build | yes (rerun setup step 2) |
| `frontend/dist/` | `npm run build` | the bundled `uvicorn x_agent.server:app` deploy | yes |
| `frontend/.vite/`, `*.tsbuildinfo` | Vite + tsc caches | nothing user-facing | yes |
| `.runtime/`, `*.log` | optional server log redirection | nothing user-facing | yes |

Persona artifacts (`~/.x-agent/personas/<id>/spec.json`, `transcript.jsonl`, `embeddings.npz`) live *outside* the repo at `PERSONA_DIR`. They aren't gitignored because they aren't in the repo at all — they're in your home directory. Back them up separately if you want to keep them.

If you went the Docker route instead, the only path written under your repo is the build context Docker reads from disk; the app's state lives in a single Docker named volume (`persona_data`). Run `docker volume ls` to see it and `docker compose down -v` to wipe it. Model weights stay on your host (`~/.ollama/models`) since Ollama runs there, not in a container — see [Quickstart with Docker](#quickstart-with-docker) for the details.

The remaining `.gitignore` entries (`build/`, `dist/`, `.eggs/`, `*.egg`, `*$py.class`, `.coverage`, `htmlcov/`, `frontend/*.local`, `.idea/`, `.vscode/`, `*.swp`, `.DS_Store`, `.env.*`) are Python sdist outputs, coverage reports, IDE / editor / OS artifacts, and parallel `.env` variants — none require user action and most appear only if you run a specific tool that creates them.

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

Assuming you ran step 2 of [Setup](#setup), dev mode runs the API on `:8000` and the Vite dev server on `:5173`. Vite proxies `/api` to the API, so it's same-origin from the browser's POV.

```bash
# one-shot: start both with hot-reload (Ctrl-C kills both)
./scripts/dev.sh

# or, in two terminals
uvicorn x_agent.server:app --reload --host 127.0.0.1
cd frontend && npm run dev
```

For a single-process production deploy, build the SPA once and FastAPI will mount it at `/` via `StaticFiles`:

```bash
cd frontend && npm run build && cd ..
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

The Ollama models from [Requirements](#requirements) cover everything the persona path needs (`llama3:latest` for chat + critic, `nomic-embed-text` for transcript retrieval). No extra setup beyond the main [Setup](#setup) — persona artifacts land in `PERSONA_DIR` (default `~/.x-agent/personas/`).

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
