# Hardening

x-agent is built to run on `localhost` for a single user. This document captures the security posture, what's logged, and the explicit assumptions you're trusting.

## Trust boundaries

```
┌──────────────────────────────────────────────────────┐
│ Your machine                                         │
│                                                      │
│  Browser ── HTTP ── FastAPI ── HTTP ── Ollama (host) │
│                       │                              │
│                       └── HTTPS (research, opt-in) ──┼──> external search + page fetch
│                       │                              │
│                       └── disk (~/.x-agent/personas) │
└──────────────────────────────────────────────────────┘
```

- The FastAPI server listens on `0.0.0.0:8000` inside the container (mapped to host `:8000`). It has **no authentication**. Do not bind it to a non-loopback interface without putting an authenticated reverse proxy in front.
- The only outbound traffic this process ever makes is (a) Ollama on `localhost`, (b) web research, *only when you opt in*.
- Persona content (`spec.json`, `personality.md`, `transcript.jsonl`, `embeddings.npz`) lives on disk with `0600` files / `0700` dirs. None of it is ever logged.

## Container posture (when running via `./scripts/start.sh`)

From `Dockerfile` and `docker-compose.yml`:

- Multi-stage build; the runtime image is `python:3.12-slim-bookworm` and contains no Node, no build tools, no dev extras.
- Runs as **non-root** (uid 10001, group 10001, `nologin` shell).
- **Read-only root filesystem** (`read_only: true`); `/tmp` is a 64 MB `tmpfs`. The persona store lives on a named volume `persona_data` mounted at `/data/personas`.
- All Linux capabilities dropped (`cap_drop: ALL`), `no-new-privileges` enabled.
- `HEALTHCHECK` hits the FastAPI liveness endpoint every 30 s.
- Pinned base image tags (no `:latest`), `PYTHONDONTWRITEBYTECODE=1` so `.pyc` files don't try to write to the read-only root.

## Secrets

- Every config knob is sourced from environment variables via `pydantic-settings` (see `src/x_agent/config.py`).
- `.env` is gitignored. Only `.env.example` is tracked.
- `TAVILY_API_KEY` / `BRAVE_SEARCH_API_KEY` are wrapped in `pydantic.SecretStr` and only surfaced in `/api/health` as booleans.
- There are **no X (Twitter) credentials**. The X API integration was removed in the local-first refactor; there is nothing to leak.

## What gets logged

- Request method + path + status code at INFO.
- Persona lifecycle events (create, save, refine, delete) at INFO, keyed by persona id (which is a non-PII slug).
- Draft graph node transitions at DEBUG.
- LLM prompts and responses **are not** logged. Persona contents **are not** logged. Web research URLs are logged at INFO but the response bodies are not.

## SSRF / DoS controls on web research

See [research.md](research.md#hardening). In short: scheme allow-list, private-IP check after DNS resolution but before the request, 10 s per-request timeout, 1 MB response cap, ~12 s overall wall-clock cap, redirects disabled.

## Input shaping

- Topic input is length-bounded (`MAX_TOPIC_CHARS`, default 2 000) and stripped of control characters before reaching the LLM.
- Persona answers are length-bounded (`MAX_ANSWER_CHARS`, default 20 000).
- `MAX_TWEET_CHARS` caps each formatted post (default 280).

## Disclosure enforcement

For `is_real_person=true` personas, the disclosure tag (e.g. `[AI persona of @handle]`) is auto-injected at `human_review.approve` time **server-side**. Editing the post in the UI cannot remove it; either the tag appears in the last tweet or, if it would push the first tweet over the char cap, the disclosure becomes its own final tweet. This is by design — the rule lives in `nodes.py::_enforce_disclosure`, not in the client.

## What this tool does *not* do

- Authenticate users. Run it locally.
- Make outbound writes anywhere. There is no posting, no telemetry, no "phone home".
- Persist conversational history beyond `~/.x-agent/personas`. Closing the browser ends the session; the LangGraph `MemorySaver` is in-process and is wiped on server restart (the persona artifacts on disk are durable).
- Sign or verify artifacts. The persona files are plain JSON / JSONL / markdown / npz.

## Threat model in one paragraph

You are the operator and the user. The threats we defend against are: a hostile URL handed to the research subsystem (handled by SSRF/DoS guards), a buggy LLM trying to write a `.pyc` to root (handled by the read-only root), and accidental credential leaks (handled by removing X creds entirely and wrapping the rest in `SecretStr`). We do **not** defend against: a hostile multi-tenant LAN (don't bind off-localhost), a compromised host (game over), or a hostile Ollama model (you chose to run it). If your threat model needs more than this, run x-agent inside an airgapped VM.
