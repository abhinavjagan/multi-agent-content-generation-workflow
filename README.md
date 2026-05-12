# x-agent

A **local-only**, persona-driven content generation tool. Capture how a person actually talks (consent + interview → markdown profile + embeddings), draft posts in their voice using a **local Ollama** model, review/edit them in a clean web UI, and walk away with a copy-ready single tweet or numbered thread.

```
topic → load persona → (optional) research → draft → format → persona critic → human review → copy / open X compose
```

**x-agent never posts for you.** There is no X (Twitter) API integration, no OAuth, no automated publishing. After approval you get a polished artifact you can paste into your own tools — that's it.

## Quickstart

```bash
git clone https://github.com/abhinavjagan/multi-agent-content-generation-workflow.git
cd multi-agent-content-generation-workflow
./scripts/start.sh
```

That script does the whole song-and-dance:

1. Checks Docker and Ollama are installed and reachable.
2. Pulls `llama3:latest` (chat + critic) and `nomic-embed-text` (persona retrieval) on the host if missing.
3. Copies `.env.example` → `.env` if you don't have one.
4. Starts the host-side **voice sidecar** (`scripts/start_voice.sh`) — calls `scripts/download_models.sh` to stage Kokoro TTS + faster-whisper STT into `~/.x-agent/models` with `curl` (host trust store), then runs uvicorn on `127.0.0.1:8765`.
5. Builds and starts the FastAPI container; the container proxies `/api/voice/*` to the host sidecar (`VOICE_REMOTE_URL=http://host.docker.internal:8765`).
6. Polls `/api/health` until it's green, then prints the UI URL.

Open **<http://localhost:8000>** and you're in. Stop with `./scripts/stop.sh` (add `--wipe` to also drop the persona volume).

> **Why is Ollama (and voice) on the host?** Docker on macOS can't pass Metal to a container, so containerized Ollama is CPU-only and ~10× slower. The voice sidecar follows the same pattern: keeping it on the host means model downloads use the OS keychain (works behind Cisco/Zscaler TLS interception), Kokoro+faster-whisper share Apple Silicon kernels, and the FastAPI container itself needs zero outbound network. Linux works the same way: just `ollama serve` on the host before running `./scripts/start.sh`.

## Requirements

- **Docker** with Compose v2 (Docker Desktop, Colima, or Docker Engine + compose plugin).
- **[Ollama](https://ollama.com)** running on the host (`brew install ollama` on macOS, `curl -fsSL https://ollama.com/install.sh | sh` on Linux).
- ~5 GB of disk for the two Ollama models.

That's it for the Quickstart path. If you'd rather run the FastAPI server natively (no Docker), see [docs/architecture.md](docs/architecture.md#native-runtime).

## Use it

1. **Make a persona.** Open the UI → *Personas → New persona*. Walk through the interview (~17 questions, or 6 in Quick mode). Your answers persist progressively — close the tab and resume later.
2. **Draft a post.** *Draft → pick the persona, type a topic, click Generate*. Optionally turn on web research to ground the draft in a URL or a search result.
3. **Review.** Approve, edit in place, or regenerate. The persona consistency critic runs automatically and loops if the score is too low.
4. **Copy.** On approve you get a *FinalDraft* card with copy-all, copy-each-tweet, and an "Open X compose" deep link. The deep link populates X's own composer with the text — *you* hit publish there.

The same flows are available on the CLI inside the container:

```bash
docker compose exec app x-agent persona create --name "Abhi" --handle abhi --quick
docker compose exec app x-agent generate "what makes a good distributed-tracing span name" \
  --persona abhi-XXXXXXXX --mode thread
docker compose exec app x-agent persona edit-md abhi-XXXXXXXX   # opens personality.md in $EDITOR
```

## What lives where

| Path | What's in it |
| --- | --- |
| `~/.x-agent/personas/<id>/spec.json` | structured persona (voice, values, idioms, banned phrases…) |
| `~/.x-agent/personas/<id>/personality.md` | long-form persona profile the writer prompt actually reads |
| `~/.x-agent/personas/<id>/transcript.jsonl` | raw Q+A from the interview |
| `~/.x-agent/personas/<id>/embeddings.npz` | retrieval index over the transcript |
| Docker volume `persona_data` | the same tree, when running via `./scripts/start.sh` |

Files are `0600`, directories `0700`. Persona contents are never logged. Back them up with `docker volume export persona_data` (or `cp -R ~/.x-agent/personas …` when running natively).

## Configuration

`.env.example` is the single source of truth. Skim it once. Highlights:

- **`OLLAMA_BASE_URL`** — defaults to `http://localhost:11434` natively and `http://host.docker.internal:11434` inside Docker.
- **`OLLAMA_MODEL`** / **`EMBEDDING_MODEL`** — pick any tag from `ollama list`.
- **`PERSONA_DIR`** — where personas live (`/data/personas` inside the container, `~/.x-agent/personas` natively).
- **`MAX_TWEET_CHARS`** — formatter cap; default 280.
- **`RESEARCH_PROVIDER`** — `auto` (Tavily → Brave → DuckDuckGo), or pin one. No keys required; DuckDuckGo works out of the box.

## Deep dives

- [docs/architecture.md](docs/architecture.md) — the LangGraph state machine, nodes, and runtime layout (including native-only deployment).
- [docs/persona.md](docs/persona.md) — how the interview, the markdown profile, the critic, and the embedded retrieval index fit together.
- [docs/research.md](docs/research.md) — optional web research, SSRF/DoS guards, provider selection.
- [docs/hardening.md](docs/hardening.md) — container posture, secret handling, what is and isn't logged.
- [docs/testing.md](docs/testing.md) — `pytest`, smoke scripts, frontend type-check, what each one actually exercises.

## Security notes

x-agent is designed to run on `localhost` for a single user. There is no auth on the FastAPI server — put it behind an authenticated reverse proxy if you ever bind to a non-loopback interface. The only outbound HTTP this process ever makes is (a) Ollama on `localhost`, (b) web research when *you* opt in. Persona content never leaves your machine.

## License & disclaimer

MIT. Personas of real people require the subject's consent, and every generated post carries an automatic AI-persona disclosure. Don't impersonate without permission — that's on you, not the tool.
