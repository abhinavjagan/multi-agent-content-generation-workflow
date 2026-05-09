"""Web research subsystem.

Two capabilities, both gated by an explicit ``research_enabled`` flag from
the caller (CLI flag, API request body, or graph state):

- **URL fetching**: the user pastes one or more URLs, the agent extracts
  article text from each via ``trafilatura`` and uses it to ground the
  draft.
- **Topic-driven search**: the user enables research without URLs, the
  agent searches the web (DuckDuckGo by default; Tavily / Brave when their
  API keys are configured), then fetches the top-k pages.

Why this lives in its own module:

- The rest of x-agent is local-only (Ollama on localhost, persona store
  on the local disk). Research is the FIRST outbound network call this
  app makes; isolating it makes the security boundary explicit.
- Providers are pluggable so adding a new search backend is a small
  ``BaseProvider`` subclass, not a graph rewrite.

Hardening summary (matches the workspace ``codeguard`` rules):

- SSRF: ``fetcher`` parses the URL, restricts schemes to http/https, and
  resolves every IP for the host before the request, rejecting
  private/loopback/link-local/reserved/multicast addresses. Redirects are
  disabled at the HTTP client to prevent redirect-based SSRF.
- DoS: per-fetch timeout (default 10s) and per-response byte cap (1 MB);
  overall ``gather_research`` honors a wall-clock cap.
- Privacy: the chosen provider is surfaced in ``/api/health`` so the user
  can see who their query goes to. API keys are ``SecretStr`` and are
  exposed only as booleans.
"""

from .compose import gather_research
from .providers import (
    BaseProvider,
    BraveProvider,
    DuckDuckGoProvider,
    TavilyProvider,
    provider_name,
    select_provider,
)
from .schema import WebResult

__all__ = [
    "BaseProvider",
    "BraveProvider",
    "DuckDuckGoProvider",
    "TavilyProvider",
    "WebResult",
    "gather_research",
    "provider_name",
    "select_provider",
]
