"""Orchestrate provider search + URL fetching into a single result list.

Two flows, picked by what the caller passes:

- ``urls`` is non-empty -> fetch each URL directly. Search is skipped
  entirely; the user has explicitly told us where to look.
- ``urls`` is empty -> run the search provider for ``query``, take the
  top-``k`` hits, fetch each in parallel for article text. The merged
  ``WebResult`` carries provider snippet + extracted content.

Network calls fan out via ``ThreadPoolExecutor`` (max 4 workers) and a
hard wall-clock cap so a single slow host can never hang the agent.
"""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Any
from urllib.parse import urlparse

from .fetcher import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_CONTENT_CHARS,
    DEFAULT_TIMEOUT_S,
    FetchError,
    fetch_url,
)
from .providers import BaseProvider, select_provider
from .schema import WebResult

log = logging.getLogger(__name__)


_MAX_PARALLEL_FETCHES = 4
_OVERALL_DEADLINE_S = 12.0


def _canonical_url(url: str) -> str:
    """Normalise URLs for dedupe: lowercase scheme+host, strip trailing slash."""
    try:
        p = urlparse(str(url))
    except Exception:  # noqa: BLE001
        return str(url)
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    path = p.path or "/"
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    return f"{scheme}://{host}{path}{('?' + p.query) if p.query else ''}"


def gather_research(
    *,
    query: str | None,
    urls: list[str] | None,
    k: int,
    settings: Any,
    provider: BaseProvider | None = None,
    fetch_fn: Any = None,
) -> list[WebResult]:
    """Compose search + fetch into a deduped list of :class:`WebResult`.

    Parameters
    ----------
    query
        Search query (used only when ``urls`` is empty/None).
    urls
        Explicit URLs to fetch. When non-empty, search is skipped.
    k
        Max number of results to return.
    settings
        x-agent ``Settings`` (see ``config.py``); used for provider
        selection and per-fetch limits.
    provider
        Override the search provider (used in tests).
    fetch_fn
        Override the fetcher (used in tests). Must have the signature of
        :func:`fetcher.fetch_url`.
    """
    k = max(1, min(int(k or 4), 10))
    fetch = fetch_fn or fetch_url
    timeout = float(getattr(settings, "research_fetch_timeout_s", DEFAULT_TIMEOUT_S))
    max_chars = int(
        getattr(settings, "research_max_content_chars", DEFAULT_MAX_CONTENT_CHARS)
    )
    max_bytes = DEFAULT_MAX_BYTES

    cleaned_urls = _clean_urls(urls)
    if cleaned_urls:
        log.info(
            "gather_research: url-mode urls=%d query_len=%d",
            len(cleaned_urls), len(query or ""),
        )
        return _fetch_many(
            cleaned_urls[:k], fetch, timeout=timeout,
            max_bytes=max_bytes, max_chars=max_chars,
        )

    q = (query or "").strip()
    if not q:
        log.info("gather_research: no urls and no query; returning empty")
        return []

    chosen = provider or select_provider(settings)
    log.info(
        "gather_research: search-mode provider=%s k=%d query_len=%d",
        chosen.name, k, len(q),
    )

    hits = chosen.search(q, k=k)
    if not hits:
        return []

    # Fetch the top hits in parallel; merge extracted content back into
    # the corresponding hit so the prompt carries both snippet and body.
    fetched_by_url = {
        _canonical_url(str(r.url)): r
        for r in _fetch_many(
            [str(h.url) for h in hits], fetch,
            timeout=timeout, max_bytes=max_bytes, max_chars=max_chars,
        )
    }

    merged: list[WebResult] = []
    seen: set[str] = set()
    for hit in hits:
        canon = _canonical_url(str(hit.url))
        if canon in seen:
            continue
        seen.add(canon)
        fetched = fetched_by_url.get(canon)
        if fetched and fetched.content:
            merged.append(
                hit.model_copy(
                    update={
                        "content": fetched.content,
                        "title": hit.title or fetched.title,
                        "source": "search",
                    }
                )
            )
        else:
            merged.append(hit)
    return merged[:k]


def _clean_urls(urls: list[str] | None) -> list[str]:
    """Trim, dedupe (preserve order), drop empties; cap the total at 5.

    The 5-URL cap is also enforced server-side in the API layer; we
    re-apply it here so the CLI can't over-share either.
    """
    if not urls:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not u:
            continue
        canon = _canonical_url(u)
        if canon in seen:
            continue
        seen.add(canon)
        out.append(u)
        if len(out) >= 5:
            break
    return out


def _fetch_many(
    urls: list[str],
    fetch: Any,
    *,
    timeout: float,
    max_bytes: int,
    max_chars: int,
) -> list[WebResult]:
    """Fan out URL fetches; honour the overall wall-clock budget."""
    results: list[WebResult] = []
    if not urls:
        return results
    workers = min(_MAX_PARALLEL_FETCHES, len(urls))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_url = {
            pool.submit(
                fetch, u,
                timeout=timeout, max_bytes=max_bytes, max_chars=max_chars,
            ): u
            for u in urls
        }
        try:
            for fut in concurrent.futures.as_completed(
                future_to_url, timeout=_OVERALL_DEADLINE_S,
            ):
                url = future_to_url[fut]
                try:
                    results.append(fut.result())
                except FetchError as exc:
                    log.warning(
                        "fetch failed host=%s reason=%s", _host(url), exc,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "fetch crashed host=%s exc=%s",
                        _host(url), type(exc).__name__,
                    )
        except concurrent.futures.TimeoutError:
            log.warning(
                "research wall-clock cap (%.1fs) reached; partial results=%d",
                _OVERALL_DEADLINE_S, len(results),
            )
            for fut in future_to_url:
                fut.cancel()
    return results


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname or "?"
    except Exception:  # noqa: BLE001
        return "?"
