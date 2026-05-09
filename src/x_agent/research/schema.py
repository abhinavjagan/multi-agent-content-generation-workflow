"""Pydantic models for web research results."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class WebResult(BaseModel):
    """A single source the agent saw.

    Either produced by a search provider (``source='search'``) or by direct
    URL fetch (``source='fetched'``). The two stages are merged in
    ``compose.gather_research`` so the final list always includes both
    snippets and (where available) the extracted article text.

    Field caps below are defensive: they bound the size of the prompt block
    we feed to the LLM and the JSON we return over the API. Length is
    enforced again at the API layer for incoming user URLs.
    """

    url: HttpUrl
    title: str = Field(default="", max_length=500)
    snippet: str = Field(
        default="",
        max_length=2000,
        description="Short excerpt from the search provider, if any.",
    )
    content: str = Field(
        default="",
        max_length=32_000,
        description=(
            "Plain-text article body extracted from the page (when fetched). "
            "Already truncated to the configured ``research_max_content_chars``."
        ),
    )
    source: Literal["search", "fetched"]
    provider: str | None = Field(
        default=None,
        max_length=32,
        description="Search provider name, when applicable.",
    )
    score: float | None = Field(
        default=None,
        description="Provider-supplied relevance score, when available (Tavily).",
    )

    def host(self) -> str:
        """Hostname for compact UI display + safe logging."""
        return str(self.url).split("/", 3)[2] if "//" in str(self.url) else ""

    def short(self, n: int = 280) -> str:
        """Best available text under ``n`` chars: content if extracted else snippet."""
        text = (self.content or self.snippet or "").strip()
        return text if len(text) <= n else text[: n - 1].rstrip() + "\u2026"
