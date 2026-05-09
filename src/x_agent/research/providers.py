"""Pluggable search providers for web research.

All providers implement :meth:`BaseProvider.search` and return a list of
``WebResult`` (with ``source='search'`` and no ``content`` yet -- the
fetcher fills ``content`` if requested). Failures NEVER raise out of the
provider: a network blip, an expired API key, or a malformed payload all
degrade to an empty list with a logged warning. The graph still runs;
``generate_draft`` just sees an empty ``WEB CONTEXT`` block.

Providers are picked up by ``select_provider`` at request time based on
``Settings``. The default is DuckDuckGo (no API key, free) so the feature
works out-of-the-box on a fresh checkout.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from .schema import WebResult

log = logging.getLogger(__name__)


class BaseProvider(ABC):
    """Abstract search provider."""

    name: str = "base"

    @abstractmethod
    def search(self, query: str, *, k: int) -> list[WebResult]:
        """Return up to ``k`` search hits for ``query``.

        Implementations must never raise; on any error return ``[]`` and
        log at WARNING.
        """


# --------------------------------------------------------- DuckDuckGo (no key)


class DuckDuckGoProvider(BaseProvider):
    """DuckDuckGo Instant Answer / HTML scrape via the ``ddgs`` library.

    No API key needed. Quality varies, but it's perfect for the default
    out-of-the-box path. ``ddgs`` does the rate-limiting and parsing.
    """

    name = "duckduckgo"

    def search(self, query: str, *, k: int) -> list[WebResult]:
        try:
            from ddgs import DDGS  # imported lazily so unit tests can mock
        except ImportError:  # pragma: no cover - dep is required
            log.warning("ddg search: 'ddgs' package missing; install x-agent[dev]")
            return []

        try:
            raw = DDGS().text(query, max_results=max(1, k))
        except Exception as exc:  # noqa: BLE001 - never break the graph
            log.warning("ddg search failed (%s): %s", type(exc).__name__, exc)
            return []

        out: list[WebResult] = []
        for item in raw[:k]:
            url = (item.get("href") or item.get("url") or "").strip()
            if not url:
                continue
            try:
                out.append(
                    WebResult(
                        url=url,
                        title=str(item.get("title") or "")[:500],
                        snippet=str(item.get("body") or "")[:2000],
                        content="",
                        source="search",
                        provider=self.name,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - bad payload -> skip
                log.debug("ddg: dropping bad row: %s", exc)
                continue
        return out


# ------------------------------------------------------------- Tavily (key)


class TavilyProvider(BaseProvider):
    """Tavily Search API (https://tavily.com).

    AI-optimised, returns concise snippets and a ``score`` in [0, 1]. Free
    tier: 1000 requests / month. We only call the cheap ``search``
    endpoint here; the deeper ``content`` field would cost extra credits
    and we have our own fetcher anyway.
    """

    name = "tavily"
    _ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str, *, timeout_s: float = 8.0) -> None:
        if not api_key:
            raise ValueError("TavilyProvider requires a non-empty api_key")
        self._api_key = api_key
        self._timeout = timeout_s

    def search(self, query: str, *, k: int) -> list[WebResult]:
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max(1, min(10, k)),
            "include_answer": False,
            "include_raw_content": False,
            "search_depth": "basic",
        }
        try:
            resp = httpx.post(
                self._ENDPOINT,
                json=payload,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("tavily search failed (%s): %s", type(exc).__name__, exc)
            return []

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return []

        out: list[WebResult] = []
        for item in results[:k]:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            try:
                out.append(
                    WebResult(
                        url=url,
                        title=str(item.get("title") or "")[:500],
                        snippet=str(item.get("content") or "")[:2000],
                        content="",
                        source="search",
                        provider=self.name,
                        score=_safe_float(item.get("score")),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("tavily: dropping bad row: %s", exc)
                continue
        return out


# -------------------------------------------------------------- Brave (key)


class BraveProvider(BaseProvider):
    """Brave Search API (https://search.brave.com/help/api).

    Requires ``BRAVE_SEARCH_API_KEY``. Free tier: 2000 queries / month at
    1 qps. The web/search endpoint returns a ``web.results`` list with
    ``title``, ``url``, ``description``.
    """

    name = "brave"
    _ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, *, timeout_s: float = 8.0) -> None:
        if not api_key:
            raise ValueError("BraveProvider requires a non-empty api_key")
        self._api_key = api_key
        self._timeout = timeout_s

    def search(self, query: str, *, k: int) -> list[WebResult]:
        params = {"q": query, "count": str(max(1, min(20, k)))}
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._api_key,
        }
        try:
            resp = httpx.get(
                self._ENDPOINT,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("brave search failed (%s): %s", type(exc).__name__, exc)
            return []

        web = data.get("web") if isinstance(data, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        if not isinstance(results, list):
            return []

        out: list[WebResult] = []
        for item in results[:k]:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            try:
                out.append(
                    WebResult(
                        url=url,
                        title=str(item.get("title") or "")[:500],
                        snippet=str(item.get("description") or "")[:2000],
                        content="",
                        source="search",
                        provider=self.name,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("brave: dropping bad row: %s", exc)
                continue
        return out


# -------------------------------------------------------------- selection


def select_provider(settings: Any) -> BaseProvider:
    """Pick the best provider given current settings.

    Order:
    1. ``settings.research_provider`` if set to a specific provider.
    2. ``auto`` -> Tavily key set? use Tavily. Else Brave key set? use
       Brave. Else DuckDuckGo (no key needed).

    Returns a usable provider, never ``None``.
    """
    pref = (getattr(settings, "research_provider", None) or "auto").lower()
    tavily_key = _secret(getattr(settings, "tavily_api_key", None))
    brave_key = _secret(getattr(settings, "brave_search_api_key", None))

    if pref == "tavily" or (pref == "auto" and tavily_key):
        if tavily_key:
            return TavilyProvider(tavily_key)
        log.warning("research_provider=tavily but TAVILY_API_KEY missing; falling back")
    if pref == "brave" or (pref == "auto" and brave_key):
        if brave_key:
            return BraveProvider(brave_key)
        log.warning(
            "research_provider=brave but BRAVE_SEARCH_API_KEY missing; falling back"
        )
    return DuckDuckGoProvider()


def provider_name(settings: Any) -> str:
    """Resolved provider name for /api/health and the UI."""
    return select_provider(settings).name


def _secret(value: Any) -> str | None:
    """Pull a string out of a Pydantic ``SecretStr`` or a raw env var."""
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        v = getter()
        return v if v else None
    return str(value) or None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
