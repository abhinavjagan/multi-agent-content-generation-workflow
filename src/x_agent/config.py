"""Application configuration loaded from environment variables.

All secrets (X API tokens) are loaded here and never logged. The
``Settings`` object is the single source of truth for runtime config.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    Values are loaded (in order of precedence) from process env vars,
    then a local ``.env`` file. Missing X API tokens are *not* fatal at
    construction time -- callers that need them must check ``has_x_credentials``
    so that ``--dry-run`` flows still work without secrets.
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

    # --- X (Twitter) API v2 OAuth 1.0a user-context tokens ---
    x_api_key: SecretStr | None = Field(default=None)
    x_api_secret: SecretStr | None = Field(default=None)
    x_access_token: SecretStr | None = Field(default=None)
    x_access_token_secret: SecretStr | None = Field(default=None)

    # --- Behavior ---
    x_max_tweet_chars: int = Field(
        default=275,
        ge=50,
        le=280,
        description=(
            "Hard ceiling per tweet. Standard X limit is 280; we leave a small "
            "safety margin for thread numbering and trailing whitespace."
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

    @property
    def has_x_credentials(self) -> bool:
        """True only if all four OAuth 1.0a tokens are present."""
        return all(
            v is not None and v.get_secret_value().strip() != ""
            for v in (
                self.x_api_key,
                self.x_api_secret,
                self.x_access_token,
                self.x_access_token_secret,
            )
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
