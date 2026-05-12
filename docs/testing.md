# Testing

`pytest` is fully offline and does not need Ollama running. The smoke scripts exercise the full graph and *do* need a real Ollama daemon.

## Backend unit + integration tests

```bash
pytest -q
```

What's covered:

- The draft graph end-to-end with a mocked Ollama (in `tests/test_graph.py`).
- The interview graph with mocked extraction (`tests/test_interview_graph.py`).
- Every persona surface: schema validation, store I/O, markdown renderer, critic scoring, embedder shape (`tests/test_persona_*`).
- Web research SSRF / size / timeout guards (`tests/test_research_*`).
- FastAPI route shapes against the Pydantic models (`tests/test_server.py`).

There are no live network calls in `pytest`; the research tests use a stub `httpx.MockTransport`.

## Smoke tests (real Ollama)

These run the *real* graph against your local Ollama daemon and stop at the human review step.

```bash
# generate, format, persona-critique, present for review
python scripts/smoke.py "How to debug a memory leak in a long-running Python service" thread

# full persona path: canned interview -> spec -> personality.md -> draft -> critic
python scripts/smoke_persona.py
```

Override the model with `OLLAMA_MODEL=llama3.2:latest python scripts/smoke.py ...`.

The smoke scripts never auto-approve. They print the formatted thread and exit — there is no posting and no automation past the review step (since there's no API to post to).

## Frontend

```bash
cd frontend
npm run typecheck   # tsc --noEmit
npm run build       # vite build → frontend/dist (what FastAPI serves at /)
npm run lint        # eslint on src/
```

The frontend has no Jest/Vitest suite yet — the UI is exercised manually and via the FastAPI integration tests on the server side.

## Continuous integration

The Dockerfile deliberately omits dev extras from the runtime image. To run tests against the same code path the production image uses:

```bash
# in the repo root, native python:
pip install -e ".[dev]"
pytest -q
mypy src
ruff check src tests
```

Inside the running container the `dev` extras are *not* installed; tests run on the host instead.
