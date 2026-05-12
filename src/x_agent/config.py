"""Application configuration loaded from environment variables.

The ``Settings`` object is the single source of truth for runtime
config. x-agent never publishes anywhere, so there are no API secrets
here -- only local Ollama, persona, and (optional) research provider
knobs.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    Values are loaded (in order of precedence) from process env vars,
    then a local ``.env`` file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Ollama ---
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Base URL for the local Ollama server.",
    )
    ollama_model: str = Field(
        default="llama3:latest",
        description=(
            "Ollama model tag to use for generation. Override via OLLAMA_MODEL "
            "in .env or the --model flag on `x-agent draft`."
        ),
    )

    # --- Formatter / output shape ---
    # Standard X limit is 280; we leave a small safety margin for thread
    # numbering and trailing whitespace. Tunable via MAX_TWEET_CHARS in
    # .env. The legacy X_MAX_TWEET_CHARS name is also accepted so existing
    # .env files keep working after the rename.
    max_tweet_chars: int = Field(
        default=275,
        ge=50,
        le=280,
        validation_alias=AliasChoices("MAX_TWEET_CHARS", "X_MAX_TWEET_CHARS"),
        description=(
            "Hard ceiling per tweet output, used by the thread formatter. "
            "Defaults to 275 so a 280-char post leaves room for numbering."
        ),
    )

    # --- Persona ---
    persona_dir: str = Field(
        default="~/.x-agent/personas",
        description="Where persona specs, transcripts, and embeddings live.",
    )
    embedding_model: str = Field(
        default="nomic-embed-text",
        description="Ollama embedding model used for persona retrieval.",
    )
    critic_model: str = Field(
        default="",
        description=(
            "Ollama model for the persona consistency critic. Empty means "
            "use OLLAMA_MODEL."
        ),
    )
    critic_min_score: int = Field(
        default=4, ge=0, le=5,
        description="Minimum critic score (0-5) required to advance to review.",
    )
    critic_max_attempts: int = Field(
        default=2, ge=0, le=5,
        description="Maximum regeneration attempts triggered by the critic.",
    )
    persona_top_k: int = Field(
        default=4, ge=1, le=12,
        description="Number of transcript chunks retrieved per draft.",
    )

    # --- Web research (optional, off-by-default at the request level) ---
    # The agent stays local-only until the *caller* opts into research with
    # ``research_enabled=True`` on the API/CLI. These knobs only affect what
    # happens once they do.
    research_provider: str = Field(
        default="auto",
        pattern="^(auto|ddg|tavily|brave)$",
        description=(
            "Which search backend to use when research is enabled. "
            "'auto' picks Tavily/Brave when their key is set, else DuckDuckGo."
        ),
    )
    tavily_api_key: SecretStr | None = Field(
        default=None,
        description="Optional Tavily Search API key. Surfaces in /api/health as a boolean only.",
    )
    brave_search_api_key: SecretStr | None = Field(
        default=None,
        description="Optional Brave Search API key. Surfaces in /api/health as a boolean only.",
    )
    research_max_results: int = Field(
        default=4, ge=1, le=10,
        description="Cap on URLs/search hits per research call (also caps user-supplied URLs).",
    )
    research_fetch_timeout_s: float = Field(
        default=10.0, ge=1.0, le=30.0,
        description="Per-URL HTTP timeout when fetching pages for extraction.",
    )
    research_max_content_chars: int = Field(
        default=8000, ge=500, le=32_000,
        description="Truncation cap for extracted article text per source.",
    )

    # --- Voice pipeline (TTS + STT, optional) ---
    # All-local: Kokoro-82M (ONNX) for TTS, faster-whisper for STT. Both
    # engines are lazy-loaded; if either fails to initialise the endpoints
    # return 503 and the UI hides voice controls -- the text-only flow keeps
    # working unchanged.
    voice_enabled: bool = Field(
        default=True,
        description=(
            "Master switch. When false, /api/voice/* endpoints return 503 and "
            "the UI hides mic / TTS controls; the typed-answer flow is unaffected."
        ),
    )
    voice_stt_model: str = Field(
        default="small.en",
        max_length=80,
        description=(
            "faster-whisper model tag (e.g. 'tiny.en', 'base.en', 'small.en', "
            "'small'). English-only tags are ~30% smaller. Downloaded on first "
            "use to VOICE_MODEL_DIR."
        ),
    )
    voice_stt_compute_type: str = Field(
        default="int8",
        pattern="^(int8|int8_float16|int8_float32|float16|float32)$",
        description=(
            "CTranslate2 compute precision for Whisper. 'int8' is the smallest "
            "and runs on CPU; bump to 'float16' if you have a GPU configured."
        ),
    )
    voice_tts_voice: str = Field(
        default="am_michael",
        max_length=40,
        pattern=r"^[a-z]{2}_[a-z0-9_]+$",
        description=(
            "Kokoro voice id (see hexgrad/Kokoro-82M VOICES.md). Defaults to "
            "'am_michael' (American male, clean). Common alternatives: "
            "'am_adam' (am, deeper), 'bm_george' (British male), "
            "'af_bella' (American female, warm), 'af_sarah' (American female, neutral)."
        ),
    )
    voice_tts_speed: float = Field(
        default=1.0, ge=0.5, le=2.0,
        description="Kokoro synthesis speed multiplier.",
    )
    voice_tts_lang: str = Field(
        default="en-us",
        max_length=10,
        pattern=r"^[a-z]{2}-[a-z]{2}$",
        description="Kokoro language code (e.g. 'en-us', 'en-gb').",
    )
    voice_model_dir: str = Field(
        default="~/.x-agent/models",
        description=(
            "Where faster-whisper + Kokoro ONNX weights are cached. Must be "
            "writable -- in Docker this maps to the model_cache volume."
        ),
    )
    voice_remote_url: str = Field(
        default="",
        max_length=200,
        description=(
            "Optional URL of a host-side voice sidecar (see scripts/voice_server.py). "
            "When set, /api/voice/* proxy to this URL instead of loading Kokoro / "
            "faster-whisper inside the container. Useful behind corporate TLS "
            "interception where the host trust chain differs from the container's. "
            "Inside docker-compose this is set to http://host.docker.internal:8765."
        ),
    )
    voice_max_audio_bytes: int = Field(
        default=8_000_000, ge=10_000, le=64_000_000,
        description=(
            "Hard cap on uploaded audio body size for /api/voice/transcribe. "
            "Anything larger returns 413 before the file hits disk."
        ),
    )
    voice_max_audio_seconds: int = Field(
        default=120, ge=5, le=600,
        description=(
            "Post-decode audio duration cap. Whisper runtime is roughly linear "
            "in duration; this stops a 100MB-but-short file from running for "
            "ten minutes."
        ),
    )
    voice_tts_max_chars: int = Field(
        default=800, ge=50, le=2000,
        description=(
            "Per-request synthesis text cap for /api/voice/speak. Bounds CPU "
            "time and prevents abuse of the TTS endpoint."
        ),
    )
    voice_rate_limit_per_5min: int = Field(
        default=20, ge=1, le=500,
        description=(
            "In-process per-client-IP rate limit for /api/voice/* "
            "(token bucket, 5-minute window)."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
