"""FastAPI app exposing the agent over HTTP and serving the React UI.

Routes:
- ``/api/health``: composite health (Ollama reachability, X creds presence,
  persona count, version). Never returns secret values.
- ``/api/draft`` + ``/api/approve/{thread_id}``: draft + HITL review loop.
- ``/api/personas`` family: list / show / delete / interactive interview /
  refine (batched) / resume-extract / transcript / eval (SSE).

NOTE: there is no authentication on these endpoints. Do not expose this
process to the public internet without putting an authenticated reverse
proxy in front of it; the X tokens it can use to post are highly sensitive.
Run for local use only::

    uvicorn x_agent.server:app --reload --host 127.0.0.1

In production we additionally serve the built React app at ``/`` from
``frontend/dist`` via :class:`StaticFiles`. That mount is only attached when
the directory exists, so dev (``vite`` on a separate port) keeps working.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi import File as FileParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from . import __version__
from .config import get_settings
from .formatter import sanitize_topic
from .graph import build_graph
from .interview_graph import build_interview_graph, initial_interview_state
from .nodes import research as research_node
from .nodes import run_variant
from .persona.embedder import PersonaEmbedder
from .persona.interview import extract_persona_spec
from .persona.questions import all_questions, by_dimension, quick_questions
from .persona.schema import PersonaSpec, TranscriptEntry, utcnow
from .persona.store import PersonaNotFoundError, PersonaWriteError, get_default_store
from .research import provider_name as resolve_provider_name
from .voice import KokoroTTS, VoiceEngineError, VoiceEngineUnavailable, WhisperSTT
from .voice import proxy as voice_proxy
from .voice.security import RateLimiter, client_key, validate_audio_upload

log = logging.getLogger(__name__)

app = FastAPI(title="x-agent", version=__version__)

# CORS: locked-down to local Vite dev origins only. We never wildcard,
# never enable credentials with `*`, and never enable in prod (same-origin
# via the StaticFiles mount).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
    max_age=600,
)

_graph = build_graph()
_interview_graph = build_interview_graph()

# Voice rate limiter: 5-minute sliding window. Created lazily on first
# request so a config reload picks up the new ceiling.
_voice_rate_limiter: Optional[RateLimiter] = None


def _get_voice_rate_limiter() -> RateLimiter:
    global _voice_rate_limiter
    if _voice_rate_limiter is None:
        _voice_rate_limiter = RateLimiter(
            max_per_window=get_settings().voice_rate_limit_per_5min,
            window_seconds=300,
        )
    return _voice_rate_limiter


def _enforce_voice_rate_limit(request: Request) -> None:
    """Apply the voice rate limit; raise 429 on deny."""
    if not get_settings().voice_enabled:
        raise HTTPException(status_code=503, detail="voice pipeline is disabled")
    key = client_key(request.client.host if request.client else None)
    allowed, retry_after = _get_voice_rate_limiter().allow(key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="voice rate limit exceeded",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


# ---------------------------------------------------------------- request models


# Research is the first feature in x-agent that issues outbound HTTP. We
# bound the user-supplied URL list aggressively at the API layer (5 URLs,
# each <= 2 KB) on top of the SSRF/byte/time guards inside the fetcher,
# so a careless paste can't blow up the request.
_RESEARCH_URL_MAX = 5
_RESEARCH_URL_LEN = 2048
_RESEARCH_QUERY_LEN = 500


class ResearchSettings(BaseModel):
    """Per-request research knobs shared by /api/draft and /api/draft/variants."""

    research_enabled: bool = False
    research_urls: Optional[list[str]] = Field(
        default=None,
        max_length=_RESEARCH_URL_MAX,
    )
    research_query: Optional[str] = Field(default=None, max_length=_RESEARCH_QUERY_LEN)


class WebResultOut(BaseModel):
    """Wire shape for a single web source.

    Mirrors :class:`x_agent.research.WebResult` but with ``url`` as a
    plain string (Pydantic ``HttpUrl`` round-trips would force the UI to
    deal with ``Url`` objects).
    """

    url: str
    title: str = ""
    snippet: str = ""
    content: str = ""
    source: Literal["search", "fetched"]
    provider: Optional[str] = None
    score: Optional[float] = None


class DraftRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=280)
    mode: Literal["single", "thread"] = "thread"
    style: str = Field(default="punchy, technical, plain prose", max_length=200)
    model: Optional[str] = Field(default=None, max_length=120)
    persona_id: Optional[str] = Field(default=None, max_length=80)
    # Optional: skip generation and use these as the draft. Set by the UI
    # when the user picks one of the parallel variants returned from
    # ``POST /api/draft/variants``. Each post is bounded by
    # ``max_tweet_chars`` plus a safety margin; the list itself is bounded
    # so a malicious caller cannot stuff arbitrary content.
    seed_posts: Optional[list[str]] = Field(default=None, max_length=20)
    # Web research opt-in. ``research_urls`` (when non-empty) takes
    # precedence over ``research_query``; ``research_query`` defaults to
    # ``topic``.
    research_enabled: bool = False
    research_urls: Optional[list[str]] = Field(
        default=None, max_length=_RESEARCH_URL_MAX,
    )
    research_query: Optional[str] = Field(default=None, max_length=_RESEARCH_QUERY_LEN)


class DraftVariantsRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=280)
    mode: Literal["single", "thread"] = "thread"
    style: str = Field(default="punchy, technical, plain prose", max_length=200)
    model: Optional[str] = Field(default=None, max_length=120)
    persona_id: Optional[str] = Field(default=None, max_length=80)
    n: int = Field(default=3, ge=1, le=5)
    # Run the persona consistency critic against each variant. Off by
    # default because it doubles the number of LLM calls (1 generate + 1
    # critic per variant) and Ollama serializes concurrent requests unless
    # ``OLLAMA_NUM_PARALLEL`` is set on the server. The post-pick HITL flow
    # always runs the critic, so the user still sees a score for the variant
    # they chose; this flag is only useful when the user wants to compare
    # critic scores across variants up front.
    score: bool = False
    # Same research fields as DraftRequest -- when enabled the gather runs
    # ONCE before fan-out and ``web_results`` is shared across every variant.
    research_enabled: bool = False
    research_urls: Optional[list[str]] = Field(
        default=None, max_length=_RESEARCH_URL_MAX,
    )
    research_query: Optional[str] = Field(default=None, max_length=_RESEARCH_QUERY_LEN)


class DraftVariant(BaseModel):
    index: int
    posts: list[str]
    temperature: float
    critic_score: Optional[int] = None
    critic_violations: list[str] = Field(default_factory=list)
    critic_suggestion: Optional[str] = None
    error: Optional[str] = None


class DraftVariantsResponse(BaseModel):
    topic: str
    mode: Literal["single", "thread"]
    persona_id: Optional[str] = None
    variants: list[DraftVariant]
    # Shared sources used for every variant when research_enabled was True.
    web_results: list[WebResultOut] = Field(default_factory=list)


class DraftResponse(BaseModel):
    thread_id: str
    posts: list[str]
    awaiting_review: bool
    critic_score: Optional[int] = None
    critic_violations: list[str] = Field(default_factory=list)
    web_results: list[WebResultOut] = Field(default_factory=list)


class ResearchPreviewRequest(BaseModel):
    """Preview-only research call used by the UI's source picker.

    Same shape as the research fields on :class:`DraftRequest`, with
    ``query`` instead of ``research_query`` for ergonomics. ``persona_id``
    isn't needed here because preview never touches the LLM.
    """

    query: Optional[str] = Field(default=None, max_length=_RESEARCH_QUERY_LEN)
    urls: Optional[list[str]] = Field(default=None, max_length=_RESEARCH_URL_MAX)


class ResearchPreviewResponse(BaseModel):
    provider: str
    query: str
    urls: list[str]
    results: list[WebResultOut] = Field(default_factory=list)


class ApproveRequest(BaseModel):
    action: Literal["approve", "edit", "regenerate", "reject"]
    edited: Optional[str] = Field(default=None, max_length=20_000)


class ApproveResponse(BaseModel):
    thread_id: str
    posts: list[str]
    awaiting_review: bool
    finalized: bool = False
    rejected: bool = False
    error: Optional[str] = None
    critic_score: Optional[int] = None
    critic_violations: list[str] = Field(default_factory=list)
    web_results: list[WebResultOut] = Field(default_factory=list)


class PersonaCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    is_real_person: bool = True
    disclosure_text: str = Field(default="", max_length=120)
    consent_ack: bool = False
    # Legacy boolean kept for back-compat with older clients.
    quick: bool = False
    # Preferred over ``quick``. When provided, takes precedence.
    #   quick   ~6 questions (the existing fast path)
    #   default ~26 questions (the full standard bank)
    #   deep    ~40 questions + aggressive follow-ups (recommended with voice)
    mode: Optional[Literal["quick", "default", "deep"]] = None


class PersonaQuestion(BaseModel):
    dimension: str
    prompt: str
    kind: Literal["open", "generative"]
    is_followup: bool = False


class PersonaInterviewState(BaseModel):
    thread_id: str
    persona_id: str
    awaiting_answer: bool
    question_index: int
    total: int
    question: Optional[PersonaQuestion] = None
    saved: bool = False
    error: Optional[str] = None


class PersonaAnswerRequest(BaseModel):
    answer: str = Field(..., max_length=20_000)


class PersonaSummary(BaseModel):
    id: str
    name: str
    is_real_person: bool
    voice_formality: int
    voice_brevity: str
    voice_humor: str
    updated_at: str


class TranscriptEntryOut(BaseModel):
    dimension: str
    question: str
    answer: str
    is_followup: bool = False
    is_holdout: bool = False
    timestamp: str


class QuestionOut(BaseModel):
    dimension: str
    prompt: str
    kind: Literal["open", "generative"]
    is_holdout: bool = False


class RefineEntry(BaseModel):
    dimension: str = Field(..., min_length=1, max_length=80)
    question: str = Field(..., min_length=1, max_length=2000)
    answer: str = Field(..., min_length=1, max_length=20_000)


class RefineRequest(BaseModel):
    entries: list[RefineEntry] = Field(..., max_length=200)


class PersonalityUpdateRequest(BaseModel):
    """Body for ``PUT /api/personas/{id}/personality``."""

    markdown: str = Field(..., max_length=40_000)


class PersonalityResponse(BaseModel):
    persona_id: str
    markdown: str


class EvalRequest(BaseModel):
    prompts: Optional[list[str]] = Field(default=None, max_length=64)
    mode: Literal["single", "thread"] = "single"


class HealthResponse(BaseModel):
    version: str
    ollama: dict[str, Any]
    personas: dict[str, Any]
    config: dict[str, Any]
    voice: dict[str, Any]


class VoiceSpeakRequest(BaseModel):
    """Body for ``POST /api/voice/speak``.

    ``text`` is hard-capped via the configured ``voice_tts_max_chars``;
    we expose 2000 here as the absolute Pydantic ceiling and re-validate
    against the runtime setting inside the engine.
    """

    text: str = Field(..., min_length=1, max_length=2000)
    voice: Optional[str] = Field(
        default=None, max_length=40, pattern=r"^[a-z]{2}_[a-z0-9_]+$"
    )
    speed: Optional[float] = Field(default=None, ge=0.5, le=2.0)
    lang: Optional[str] = Field(
        default=None, max_length=10, pattern=r"^[a-z]{2}-[a-z]{2}$"
    )


class VoiceTranscribeResponse(BaseModel):
    text: str
    duration_s: float
    model: str


DEFAULT_EVAL_PROMPTS = [
    "explain what idempotency means in API design",
    "share a hot take on remote work",
    "apologize for shipping a regression last week",
    "tell a small joke about deadlines",
    "disagree with the claim that microservices are always better",
    "explain why monitoring p99 latency matters",
    "share a quick tip on how to write better commit messages",
    "your view on rewriting legacy code vs. refactoring it",
]


# -------------------------------------------------------------------- helpers


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _thread_exists(graph: Any, thread_id: str) -> bool:
    """Return True iff the checkpointer has any state for ``thread_id``.

    ``MemorySaver`` (LangGraph's default in-process checkpointer) returns a
    snapshot whose ``.values`` is empty (``{}``) and whose ``.next`` is empty
    when no checkpoint has been written for the thread. We use that signal
    so the server can correctly emit ``404 unknown thread_id`` only when the
    thread really is unknown (e.g. user resumed a stale interview after a
    server restart) instead of swallowing every downstream graph exception
    as 404.
    """
    try:
        snap = graph.get_state(_config(thread_id))
    except Exception:  # pragma: no cover - defensive
        return False
    values = getattr(snap, "values", None) or {}
    next_nodes = getattr(snap, "next", None) or ()
    return bool(values) or bool(next_nodes)


def _raise_graph_error(thread_id: str, exc: Exception, graph: Any) -> "HTTPException":
    """Map a graph-invocation exception to the right HTTP status.

    - Unknown ``thread_id`` (e.g. checkpointer never saw it) → 404.
    - Sandbox / FS-permission failures while saving the persona → 500
      with the actionable hint from ``PersonaWriteError``.
    - Any other graph error → 500 with the exception class + message.
    """
    if not _thread_exists(graph, thread_id):
        log.info("api: unknown thread_id=%s (%s: %s)", thread_id, type(exc).__name__, exc)
        return HTTPException(status_code=404, detail=f"unknown thread_id: {thread_id}")
    if isinstance(exc, PersonaWriteError):
        log.error("api: persona write blocked: %s", exc)
        return HTTPException(status_code=500, detail=str(exc))
    log.exception("api: graph invocation failed for thread_id=%s", thread_id)
    return HTTPException(
        status_code=500,
        detail=f"{type(exc).__name__}: {exc}",
    )


def _extract_review(state: dict) -> Optional[dict]:
    interrupts = state.get("__interrupt__") if isinstance(state, dict) else None
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, "value", first)


def _ollama_status() -> dict[str, Any]:
    """Probe Ollama for reachability and the list of pulled tags.

    Always returns a dict; never raises. Failures are surfaced as
    ``ok=False`` with a short error message instead.
    """
    settings = get_settings()
    base = settings.ollama_base_url.rstrip("/")
    available: list[str] = []
    err: Optional[str] = None
    ok = False
    try:
        resp = httpx.get(f"{base}/api/tags", timeout=3.0)
        resp.raise_for_status()
        data = resp.json()
        available = sorted(
            {m.get("name", "") for m in data.get("models", []) if m.get("name")}
        )
        ok = True
    except Exception as exc:  # noqa: BLE001 - any failure -> degraded
        err = f"{type(exc).__name__}: {exc}"

    model = settings.ollama_model
    has_model = ok and (
        model in available or any(n.startswith(f"{model}:") for n in available)
    )
    return {
        "ok": ok,
        "base_url": base,
        "configured_model": model,
        "embedding_model": settings.embedding_model,
        "critic_model": settings.critic_model or model,
        "has_configured_model": has_model,
        "available_models": available,
        "error": err,
    }


def _persona_summary(spec: PersonaSpec) -> PersonaSummary:
    return PersonaSummary(
        id=spec.id,
        name=spec.name,
        is_real_person=spec.is_real_person,
        voice_formality=spec.voice.formality,
        voice_brevity=spec.voice.brevity,
        voice_humor=spec.voice.humor,
        updated_at=spec.updated_at.isoformat(),
    )


# -------------------------------------------------------------------- /api/health


def _voice_status(settings: Any) -> dict[str, Any]:
    """Report voice pipeline readiness for ``/api/health``.

    Never triggers a model download or engine load -- only inspects the
    cache directory (local mode) or pings ``/health`` on the sidecar
    (remote mode). The UI uses this to hide voice controls when the
    pipeline isn't ready, which keeps the typed flow unaffected.
    """
    base: dict[str, Any] = {
        "enabled": bool(settings.voice_enabled),
        "stt_model": settings.voice_stt_model,
        "tts_voice": settings.voice_tts_voice,
        "tts_lang": settings.voice_tts_lang,
        "max_audio_bytes": settings.voice_max_audio_bytes,
        "max_audio_seconds": settings.voice_max_audio_seconds,
        "tts_max_chars": settings.voice_tts_max_chars,
        "backend": "local",
        "remote_url": "",
    }

    remote = (settings.voice_remote_url or "").strip()
    if remote:
        # Remote sidecar mode: ask it. Never raises.
        info = voice_proxy.health()
        base["backend"] = "remote"
        base["remote_url"] = remote
        base["stt_ready"] = bool(info.get("stt_ready", False)) and bool(info.get("ok", False))
        base["tts_ready"] = bool(info.get("tts_ready", False)) and bool(info.get("ok", False))
        if not info.get("ok", False):
            base["error"] = str(info.get("error", "sidecar unreachable"))[:200]
        return base

    try:
        base["tts_ready"] = KokoroTTS.get().is_ready()
    except Exception:  # noqa: BLE001
        base["tts_ready"] = False
    try:
        base["stt_ready"] = WhisperSTT.get().is_ready()
    except Exception:  # noqa: BLE001
        base["stt_ready"] = False
    return base


def _research_status(settings: Any) -> dict[str, Any]:
    """Booleans and counts only -- never the actual API keys."""
    has_tavily = settings.tavily_api_key is not None and bool(
        settings.tavily_api_key.get_secret_value().strip()
    )
    has_brave = settings.brave_search_api_key is not None and bool(
        settings.brave_search_api_key.get_secret_value().strip()
    )
    return {
        "preference": settings.research_provider,
        "active_provider": resolve_provider_name(settings),
        "has_tavily_key": has_tavily,
        "has_brave_key": has_brave,
        "max_results": settings.research_max_results,
        "fetch_timeout_s": settings.research_fetch_timeout_s,
        "max_content_chars": settings.research_max_content_chars,
    }


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    store = get_default_store()
    persona_count = len(store.list_ids())
    return HealthResponse(
        version=__version__,
        ollama=_ollama_status(),
        personas={
            "count": persona_count,
            "dir": str(Path(settings.persona_dir).expanduser()),
        },
        config={
            "max_tweet_chars": settings.max_tweet_chars,
            "critic_min_score": settings.critic_min_score,
            "critic_max_attempts": settings.critic_max_attempts,
            "persona_top_k": settings.persona_top_k,
            "research": _research_status(settings),
        },
        voice=_voice_status(settings),
    )


@app.get("/healthz")
def healthz() -> dict:
    """Backwards-compatible liveness probe."""
    return {"status": "ok", "version": __version__}


# --------------------------------------------------------------- /api/draft


def _validate_research_urls(urls: Optional[list[str]]) -> list[str]:
    """Per-URL length cap on top of Pydantic's list-length cap.

    Pydantic enforces ``max_length`` on the list itself; we additionally
    enforce a per-string ceiling because the fetcher's SSRF check rejects
    anything > 2 KB and we want a clean 400 instead of an opaque 500.
    """
    if not urls:
        return []
    out: list[str] = []
    for u in urls:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not u:
            continue
        if len(u) > _RESEARCH_URL_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"research_urls entry too long (max {_RESEARCH_URL_LEN} chars)",
            )
        out.append(u)
    return out[:_RESEARCH_URL_MAX]


def _web_results_to_wire(state: Any) -> list[WebResultOut]:
    """Pull ``web_results`` out of graph state and shape for the wire."""
    if isinstance(state, dict):
        raw = state.get("web_results") or []
    else:
        raw = []
    out: list[WebResultOut] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        try:
            out.append(WebResultOut.model_validate({
                **r,
                "url": str(r.get("url", "")),
            }))
        except Exception:  # noqa: BLE001 - drop unrecognised rows
            continue
    return out


def _do_draft(req: DraftRequest) -> DraftResponse:
    try:
        topic = sanitize_topic(req.topic)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if req.persona_id and not get_default_store().exists(req.persona_id):
        raise HTTPException(
            status_code=404, detail=f"persona not found: {req.persona_id}"
        )

    research_urls = _validate_research_urls(req.research_urls)

    settings = get_settings()

    thread_id = uuid.uuid4().hex
    initial: dict = {"topic": topic, "style": req.style, "mode": req.mode}
    if req.model:
        initial["model"] = req.model
    if req.persona_id:
        initial["persona_id"] = req.persona_id
    if req.seed_posts:
        # Defensively cap each seeded post to the configured tweet limit + a
        # small slack so the rest of the pipeline (format_for_x) cannot be
        # forced to produce oversize tweets via this path.
        max_chars = settings.max_tweet_chars + 16
        cleaned = [
            (p or "").strip()[:max_chars]
            for p in req.seed_posts
            if p and p.strip()
        ]
        if cleaned:
            initial["seed_posts"] = cleaned
    if req.research_enabled:
        initial["research_enabled"] = True
        if research_urls:
            initial["research_urls"] = research_urls
        if req.research_query:
            initial["research_query"] = req.research_query.strip()

    state = _graph.invoke(initial, config=_config(thread_id))
    review = _extract_review(state)
    posts = (review or {}).get("posts", state.get("posts", []))
    return DraftResponse(
        thread_id=thread_id,
        posts=posts,
        awaiting_review=review is not None,
        critic_score=(review or {}).get("critic_score"),
        critic_violations=(review or {}).get("critic_violations") or [],
        web_results=_web_results_to_wire(state),
    )


@app.post("/api/draft", response_model=DraftResponse)
def create_draft(req: DraftRequest) -> DraftResponse:
    return _do_draft(req)


# Temperatures used for fan-out variant generation. Lower values produce
# safer / more on-rails drafts; higher values produce more daring ones. We
# pick a slice of this list based on ``n``: 1 -> [0.8]; 3 -> [0.6, 0.8, 1.0];
# 5 -> [0.5, 0.7, 0.9, 1.05, 1.2]. The persona-driven temp bump in
# ``generate_draft`` is bypassed when ``state.temperature`` is set, so each
# variant runs at exactly the temp we ask for.
_VARIANT_TEMP_PRESETS: dict[int, list[float]] = {
    1: [0.8],
    2: [0.7, 1.0],
    3: [0.6, 0.8, 1.0],
    4: [0.55, 0.75, 0.95, 1.15],
    5: [0.5, 0.7, 0.9, 1.05, 1.2],
}


@app.post("/api/draft/variants", response_model=DraftVariantsResponse)
async def create_draft_variants(req: DraftVariantsRequest) -> DraftVariantsResponse:
    """Generate N alternative drafts in parallel.

    Each variant runs the same pipeline as the HITL graph
    (``load_persona`` -> ``retrieve_examples`` -> ``generate_draft`` ->
    ``format_for_x`` -> persona critic) at a different sampling temperature,
    so the user gets meaningfully different outputs to choose from. The
    HITL interrupt is *not* entered here - this endpoint never posts. Once
    the user picks one variant on the client, the UI calls ``POST /api/draft``
    with ``seed_posts`` set to that variant's posts; that starts a normal
    HITL thread which the user can Edit / Approve / Regenerate / Reject.
    """
    try:
        topic = sanitize_topic(req.topic)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if req.persona_id and not get_default_store().exists(req.persona_id):
        raise HTTPException(
            status_code=404, detail=f"persona not found: {req.persona_id}"
        )

    research_urls = _validate_research_urls(req.research_urls)

    n = max(1, min(5, int(req.n)))
    temps = _VARIANT_TEMP_PRESETS[n]

    base_state: dict[str, Any] = {
        "topic": topic,
        "style": req.style,
        "mode": req.mode,
    }
    if req.model:
        base_state["model"] = req.model
    if req.persona_id:
        base_state["persona_id"] = req.persona_id

    # Run research ONCE up-front and share ``web_results`` across every
    # variant -- there's no point hitting the search provider / fetching
    # the same N URLs N times in parallel. ``run_variant`` notices the
    # pre-populated ``web_results`` and skips its own research call.
    web_results_state: dict[str, Any] = {}
    if req.research_enabled:
        research_state: dict[str, Any] = {
            "topic": topic,
            "research_enabled": True,
        }
        if research_urls:
            research_state["research_urls"] = research_urls
        if req.research_query:
            research_state["research_query"] = req.research_query.strip()
        web_results_state = await asyncio.to_thread(research_node, research_state)
        base_state["web_results"] = web_results_state.get("web_results") or []

    def _run_one(temp: float) -> dict[str, Any]:
        # Each fan-out task gets its own copy of state; ``run_variant``
        # mutates a local dict but never the input.
        state = dict(base_state)
        state["temperature"] = float(temp)
        try:
            return run_variant(state, score=req.score)
        except Exception as exc:  # noqa: BLE001 - never deadlock the gather
            log.exception("variant failed at temp=%.2f", temp)
            return {
                "posts": [],
                "critic_score": None,
                "critic_violations": [],
                "critic_suggestion": "",
                "temperature": temp,
                "error": f"{type(exc).__name__}: {exc}",
            }

    log.info(
        "draft.variants topic_len=%d mode=%s persona=%s n=%d temps=%s score=%s research=%s",
        len(topic), req.mode, bool(req.persona_id), n, temps, req.score, req.research_enabled,
    )
    results = await asyncio.gather(*(asyncio.to_thread(_run_one, t) for t in temps))

    variants = [
        DraftVariant(
            index=i,
            posts=list(r.get("posts") or []),
            temperature=float(r.get("temperature") or temps[i]),
            critic_score=r.get("critic_score"),
            critic_violations=list(r.get("critic_violations") or [])[:5],
            critic_suggestion=r.get("critic_suggestion") or None,
            error=r.get("error"),
        )
        for i, r in enumerate(results)
    ]
    return DraftVariantsResponse(
        topic=topic,
        mode=req.mode,
        persona_id=req.persona_id,
        variants=variants,
        web_results=_web_results_to_wire(web_results_state),
    )


# --------------------------------------------------------- /api/research/preview


@app.post("/api/research/preview", response_model=ResearchPreviewResponse)
async def preview_research(req: ResearchPreviewRequest) -> ResearchPreviewResponse:
    """Run the same research the draft path would, return sources only.

    Used by the UI to show a "preview sources" list before kicking off
    actual generation. No LLM is invoked here -- this is purely the
    research stage. Calls offload to a thread because the underlying
    fetcher is sync (httpx + concurrent.futures).
    """
    settings = get_settings()
    urls = _validate_research_urls(req.urls)
    query = (req.query or "").strip()
    if not urls and not query:
        raise HTTPException(
            status_code=400,
            detail="provide at least one of: urls, query",
        )

    state = {
        "research_enabled": True,
        "topic": query,
        "research_query": query,
        "research_urls": urls,
    }
    out_state = await asyncio.to_thread(research_node, state)
    return ResearchPreviewResponse(
        provider=resolve_provider_name(settings),
        query=query,
        urls=urls,
        results=_web_results_to_wire(out_state),
    )


# ----------------------------------------------------------- /api/approve


def _do_approve(thread_id: str, req: ApproveRequest) -> ApproveResponse:
    resume: dict = {"action": req.action}
    if req.action == "edit":
        if not req.edited:
            raise HTTPException(status_code=400, detail="edited text required for action=edit")
        resume["edited"] = req.edited

    if not _thread_exists(_graph, thread_id):
        raise HTTPException(status_code=404, detail=f"unknown thread_id: {thread_id}")

    try:
        state = _graph.invoke(Command(resume=resume), config=_config(thread_id))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to 404/500 below
        raise _raise_graph_error(thread_id, exc, _graph) from exc

    review = _extract_review(state)
    posts = (review or {}).get("posts", state.get("posts", []))
    return ApproveResponse(
        thread_id=thread_id,
        posts=posts,
        awaiting_review=review is not None,
        finalized=bool(state.get("finalized")),
        rejected=bool(state.get("rejected")),
        error=state.get("error"),
        critic_score=(review or {}).get("critic_score"),
        critic_violations=(review or {}).get("critic_violations") or [],
        web_results=_web_results_to_wire(state),
    )


@app.post("/api/approve/{thread_id}", response_model=ApproveResponse)
def approve(thread_id: str, req: ApproveRequest) -> ApproveResponse:
    return _do_approve(thread_id, req)


# --------------------------------------------------------------- /api/personas


def _interview_status(thread_id: str, state: dict) -> PersonaInterviewState:
    payload = _extract_review(state) or {}
    question_data = (
        payload.get("question") if payload.get("kind") == "interview_question" else None
    )
    persona = state.get("persona") or {}
    persona_id = state.get("persona_id") or persona.get("id", "")
    return PersonaInterviewState(
        thread_id=thread_id,
        persona_id=str(persona_id),
        awaiting_answer=question_data is not None,
        question_index=int(payload.get("question_index", 0)),
        total=int(payload.get("total", 0)),
        question=PersonaQuestion(**question_data) if question_data else None,
        saved=bool(state.get("saved")),
        error=state.get("error"),
    )


@app.post("/api/personas", response_model=PersonaInterviewState)
def start_persona_interview(req: PersonaCreateRequest) -> PersonaInterviewState:
    """Begin a persona interview; returns the first question to answer."""
    if req.is_real_person:
        if not req.consent_ack:
            raise HTTPException(
                status_code=400,
                detail="consent_ack must be true for real-person personas",
            )
        if not req.disclosure_text.strip():
            raise HTTPException(
                status_code=400,
                detail="disclosure_text is required for real-person personas",
            )
    thread_id = uuid.uuid4().hex
    state_in = initial_interview_state(
        name=req.name,
        is_real_person=req.is_real_person,
        disclosure_text=req.disclosure_text,
        consent_ack=req.consent_ack,
        quick=req.quick,
        mode=req.mode,
    )
    try:
        state = _interview_graph.invoke(dict(state_in), config=_config(thread_id))
    except PersonaWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _interview_status(thread_id, state)


@app.post("/api/personas/{thread_id}/answer", response_model=PersonaInterviewState)
def submit_persona_answer(
    thread_id: str, req: PersonaAnswerRequest
) -> PersonaInterviewState:
    """Resume an interview with the next answer."""
    if not _thread_exists(_interview_graph, thread_id):
        raise HTTPException(status_code=404, detail=f"unknown thread_id: {thread_id}")
    try:
        state = _interview_graph.invoke(
            Command(resume={"answer": req.answer}),
            config=_config(thread_id),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped below
        raise _raise_graph_error(thread_id, exc, _interview_graph) from exc
    return _interview_status(thread_id, state)


@app.get("/api/personas", response_model=list[PersonaSummary])
def list_personas() -> list[PersonaSummary]:
    store = get_default_store()
    out: list[PersonaSummary] = []
    for pid in store.list_ids():
        try:
            spec = store.load(pid)
        except Exception:  # noqa: BLE001
            continue
        out.append(_persona_summary(spec))
    return out


@app.get("/api/personas/{persona_id}")
def get_persona(persona_id: str) -> dict:
    try:
        spec = get_default_store().load(persona_id)
    except PersonaNotFoundError:
        raise HTTPException(status_code=404, detail="persona not found")
    return spec.model_dump(mode="json")


@app.get(
    "/api/personas/{persona_id}/personality",
    response_model=PersonalityResponse,
)
def get_persona_personality(persona_id: str) -> "PersonalityResponse":
    """Return the persona's long-form personality.md profile."""
    store = get_default_store()
    if not store.exists(persona_id):
        raise HTTPException(status_code=404, detail="persona not found")
    try:
        md = store.read_personality(persona_id)
    except PersonaNotFoundError:
        raise HTTPException(status_code=404, detail="persona not found")
    return PersonalityResponse(persona_id=persona_id, markdown=md)


@app.put(
    "/api/personas/{persona_id}/personality",
    response_model=PersonalityResponse,
)
def put_persona_personality(
    persona_id: str, req: "PersonalityUpdateRequest"
) -> "PersonalityResponse":
    """Overwrite the persona's personality.md profile."""
    store = get_default_store()
    if not store.exists(persona_id):
        raise HTTPException(status_code=404, detail="persona not found")
    md = (req.markdown or "").strip()
    if len(md) > 40_000:
        raise HTTPException(
            status_code=413,
            detail="personality.md too long (max 40,000 chars)",
        )
    store.write_personality(persona_id, md)
    return PersonalityResponse(persona_id=persona_id, markdown=md)


@app.get(
    "/api/personas/{persona_id}/transcript",
    response_model=list[TranscriptEntryOut],
)
def get_persona_transcript(persona_id: str) -> list[TranscriptEntryOut]:
    store = get_default_store()
    if not store.exists(persona_id):
        raise HTTPException(status_code=404, detail="persona not found")
    entries = store.read_transcript(persona_id)
    return [
        TranscriptEntryOut(
            dimension=e.dimension,
            question=e.question,
            answer=e.answer,
            is_followup=e.is_followup,
            is_holdout=e.is_holdout,
            timestamp=e.timestamp.isoformat(),
        )
        for e in entries
    ]


@app.get(
    "/api/personas/{persona_id}/refine/questions",
    response_model=list[QuestionOut],
)
def refine_questions(
    persona_id: str,
    dimension: Optional[str] = Query(default=None, max_length=80),
    quick: bool = Query(default=False),
) -> list[QuestionOut]:
    store = get_default_store()
    if not store.exists(persona_id):
        raise HTTPException(status_code=404, detail="persona not found")
    if dimension:
        questions = by_dimension(dimension)
        if not questions:
            raise HTTPException(
                status_code=400, detail=f"unknown dimension: {dimension}"
            )
    else:
        questions = quick_questions() if quick else all_questions()
    return [
        QuestionOut(
            dimension=q.dimension,
            prompt=q.prompt,
            kind=q.kind,
            is_holdout=q.is_holdout,
        )
        for q in questions
    ]


@app.post("/api/personas/{persona_id}/refine")
def refine_persona(persona_id: str, req: RefineRequest) -> dict:
    """Append answers to the transcript and re-extract the spec.

    Mirrors the CLI ``persona refine`` flow but takes a fully-collected
    batch of Q+A entries (the React wizard collects them client-side then
    submits all at once). Returns the refreshed persona spec.
    """
    store = get_default_store()
    try:
        spec = store.load(persona_id)
    except PersonaNotFoundError:
        raise HTTPException(status_code=404, detail="persona not found")

    if not req.entries:
        raise HTTPException(status_code=400, detail="no refine entries provided")

    extra = [
        TranscriptEntry(
            dimension=e.dimension,
            question=e.question,
            answer=e.answer,
            is_followup=False,
            timestamp=utcnow(),
        )
        for e in req.entries
    ]
    transcript = store.read_transcript(persona_id) + extra
    new_spec = extract_persona_spec(
        name=spec.name,
        is_real_person=spec.is_real_person,
        disclosure_text=spec.disclosure_text,
        transcript=transcript,
        persona_id=spec.id,
    )
    try:
        store.save(new_spec)
        store.overwrite_transcript(persona_id, transcript)
    except PersonaWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    embedder = PersonaEmbedder()
    try:
        ids, _texts, vectors = embedder.build_index(transcript)
        if vectors.size > 0:
            store.save_embeddings(persona_id, ids, vectors)
    except Exception as exc:  # noqa: BLE001 - embedding is best-effort
        log.warning("refine: embedding refresh failed (continuing): %s", exc)
    return new_spec.model_dump(mode="json")


@app.post("/api/personas/{persona_id}/resume-extract")
def resume_extract(persona_id: str) -> dict:
    """Re-run the LLM extractor against the saved transcript."""
    store = get_default_store()
    try:
        spec = store.load(persona_id)
    except PersonaNotFoundError:
        raise HTTPException(status_code=404, detail="persona not found")
    transcript = store.read_transcript(persona_id)
    if not transcript:
        raise HTTPException(
            status_code=400,
            detail="no transcript on disk; cannot resume extraction",
        )
    new_spec = extract_persona_spec(
        name=spec.name,
        is_real_person=spec.is_real_person,
        disclosure_text=spec.disclosure_text,
        transcript=transcript,
        persona_id=spec.id,
    )
    try:
        store.save(new_spec)
    except PersonaWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    embedder = PersonaEmbedder()
    try:
        ids, _texts, vectors = embedder.build_index(transcript)
        if vectors.size > 0:
            store.save_embeddings(persona_id, ids, vectors)
    except Exception as exc:  # noqa: BLE001
        log.warning("resume_extract: embedding refresh failed: %s", exc)
    return new_spec.model_dump(mode="json")


@app.delete("/api/personas/{persona_id}", status_code=204)
def delete_persona(persona_id: str) -> None:
    try:
        get_default_store().delete(persona_id)
    except PersonaNotFoundError:
        raise HTTPException(status_code=404, detail="persona not found")


# ----------------------------------------------------------- eval (SSE stream)


def _eval_iter(
    persona_id: str, prompts: list[str], mode: str
) -> Iterator[bytes]:
    """Yield SSE events as we score each prompt against the persona.

    Emits one ``score`` event per topic plus a final ``done`` event with the
    average. We keep the loop in a generator so FastAPI can stream chunks
    as they're produced.
    """
    from .persona.critic import score_against_persona

    store = get_default_store()
    spec = store.load(persona_id)

    eval_graph = build_graph()

    scores: list[int] = []
    for topic in prompts:
        try:
            cfg = _config(uuid.uuid4().hex)
            state = eval_graph.invoke(
                {"topic": topic, "mode": mode, "persona_id": persona_id},
                config=cfg,
            )
            interrupts = state.get("__interrupt__") if isinstance(state, dict) else None
            score = state.get("critic_score")
            violations = state.get("critic_violations") or []
            posts: list[str] = state.get("posts") or []
            if interrupts:
                payload = getattr(interrupts[0], "value", interrupts[0])
                score = payload.get("critic_score", score)
                violations = payload.get("critic_violations", violations)
                posts = payload.get("posts", posts)
            if score is None:
                result = score_against_persona(
                    draft="\n\n".join(posts),
                    persona=spec,
                    examples=state.get("retrieved_examples") or [],
                )
                score = result["score"]
                violations = result["violations"]
            score_int = int(score)
            scores.append(score_int)
            event = {
                "topic": topic,
                "score": score_int,
                "violations": list(violations)[:5],
                "posts": posts,
            }
        except Exception as exc:  # noqa: BLE001 - never deadlock on one row
            log.exception("eval row failed for topic=%r", topic)
            event = {
                "topic": topic,
                "score": None,
                "violations": [],
                "posts": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        yield f"event: score\ndata: {json.dumps(event)}\n\n".encode("utf-8")

    avg = sum(scores) / len(scores) if scores else None
    done = {"count": len(prompts), "scored": len(scores), "average": avg}
    yield f"event: done\ndata: {json.dumps(done)}\n\n".encode("utf-8")


@app.post("/api/personas/{persona_id}/eval")
def eval_persona(persona_id: str, req: EvalRequest) -> StreamingResponse:
    store = get_default_store()
    if not store.exists(persona_id):
        raise HTTPException(status_code=404, detail="persona not found")
    prompts = [p.strip() for p in (req.prompts or DEFAULT_EVAL_PROMPTS) if p and p.strip()]
    if not prompts:
        raise HTTPException(status_code=400, detail="no prompts provided")
    if len(prompts) > 64:
        raise HTTPException(status_code=400, detail="too many prompts (max 64)")
    return StreamingResponse(
        _eval_iter(persona_id, prompts, req.mode),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------- /api/voice/*


_VOICE_NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


@app.post("/api/voice/speak")
async def voice_speak(req: VoiceSpeakRequest, request: Request) -> Response:
    """Synthesize ``req.text`` to a WAV blob using Kokoro-82M.

    Returns ``audio/wav`` bytes with ``Cache-Control: no-store``. The
    engine is loaded lazily on first call; subsequent calls with the
    same text/voice/speed/lang are served from an in-memory LRU.
    """
    _enforce_voice_rate_limit(request)
    settings = get_settings()
    text = (req.text or "").strip()
    if len(text) > settings.voice_tts_max_chars:
        raise HTTPException(
            status_code=413,
            detail=f"text too long (max {settings.voice_tts_max_chars} chars)",
        )

    try:
        if voice_proxy.remote_url():
            # Sidecar mode: host-side service handles the model + decode.
            wav = await asyncio.to_thread(
                voice_proxy.speak,
                text,
                voice=req.voice,
                speed=req.speed,
                lang=req.lang,
            )
        else:
            wav = await asyncio.to_thread(
                KokoroTTS.get().synthesize,
                text,
                voice=req.voice,
                speed=req.speed,
                lang=req.lang,
            )
    except VoiceEngineUnavailable as exc:
        log.warning("voice.speak unavailable: %s", exc)
        raise HTTPException(
            status_code=503, detail=f"TTS engine unavailable: {exc}"
        ) from exc
    except VoiceEngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(
        content=wav,
        media_type="audio/wav",
        headers=_VOICE_NO_STORE_HEADERS,
    )


@app.post(
    "/api/voice/transcribe",
    response_model=VoiceTranscribeResponse,
)
async def voice_transcribe(
    request: Request,
    audio: UploadFile = FileParam(...),
) -> VoiceTranscribeResponse:
    """Transcribe an uploaded audio blob via faster-whisper.

    Expects a single multipart ``audio`` field. Bytes are buffered in
    memory (capped), validated by magic-byte sniffing, then dispatched
    to a worker thread. The raw audio is never written to a persistent
    location and is unlinked from /tmp immediately after transcription.
    """
    _enforce_voice_rate_limit(request)
    settings = get_settings()
    content_type = audio.content_type or ""

    # Read the body with a hard ceiling. ``UploadFile.read(n)`` returns
    # at most n bytes; if more arrive we reject with 413. We refuse to
    # buffer the rest -- the SpooledTemporaryFile would silently spill
    # to disk via tempfile, and we want body-size enforcement at the
    # *network* boundary.
    cap = int(settings.voice_max_audio_bytes)
    body = await audio.read(cap + 1)
    if len(body) > cap:
        raise HTTPException(
            status_code=413,
            detail=f"audio body too large (>{cap} bytes)",
        )

    suffix = validate_audio_upload(
        content_type=content_type,
        body=body,
        max_bytes=cap,
    )

    try:
        if voice_proxy.remote_url():
            # Sidecar mode: forward the *already-validated* bytes. The
            # sidecar re-runs the same allowlist + magic-byte sniff
            # before invoking faster-whisper.
            text, duration = await asyncio.to_thread(
                voice_proxy.transcribe, body, content_type=content_type
            )
        else:
            text, duration = await asyncio.to_thread(
                WhisperSTT.get().transcribe, body, suffix=suffix
            )
    except VoiceEngineUnavailable as exc:
        log.warning("voice.transcribe unavailable: %s", exc)
        raise HTTPException(
            status_code=503, detail=f"STT engine unavailable: {exc}"
        ) from exc
    except VoiceEngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Duration cap is post-decode: ffmpeg has already inspected the file
    # by the time faster-whisper gives us info.duration. We still return
    # what we transcribed because re-uploading would just blow the limit
    # again -- but we surface the cap as a 413 if we exceed it.
    if duration > settings.voice_max_audio_seconds:
        raise HTTPException(
            status_code=413,
            detail=(
                f"audio duration too long ({duration:.1f}s > "
                f"{settings.voice_max_audio_seconds}s)"
            ),
        )

    return VoiceTranscribeResponse(
        text=text,
        duration_s=float(duration),
        model=settings.voice_stt_model,
    )


# ----------------------------------------------------------------- static UI


class SPAStaticFiles(StaticFiles):
    """`StaticFiles` that falls back to ``index.html`` on 404.

    Starlette's built-in ``html=True`` only serves ``index.html`` for
    *directory* requests, which means SPA deep links like ``/draft`` or
    ``/personas/<id>`` 404 on full-page refresh and on shared URLs. We
    catch the 404 and return ``index.html`` instead so the React Router
    boot logic can handle the route client-side. Defined API routes are
    unaffected because they're registered on the FastAPI app before this
    mount, and Starlette matches them first; *unknown* ``/api/*`` paths
    must still 404 (as JSON) rather than getting an HTML page back, so
    we explicitly opt out of the fallback for that prefix.
    """

    _SPA_FALLBACK_OPT_OUT = ("api/", "healthz", "openapi", "docs", "redoc")

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            normalized = path.lstrip("/")
            if normalized.startswith(self._SPA_FALLBACK_OPT_OUT):
                raise
            return await super().get_response("index.html", scope)


# Mount the built React app at "/" if it exists. We resolve relative to the
# package source so a working tree layout (frontend/dist next to src/) just
# works.
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount(
        "/",
        SPAStaticFiles(directory=str(_FRONTEND_DIST), html=True),
        name="frontend",
    )
    log.info("server: serving SPA from %s", _FRONTEND_DIST)
else:  # dev mode - the Vite dev server lives on :5173
    log.info(
        "server: %s not found; SPA not mounted (run `npm run dev` in frontend/)",
        _FRONTEND_DIST,
    )
