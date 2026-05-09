"""SSRF-hardened URL fetcher with article-text extraction.

This is the only component in x-agent that issues outbound HTTP to
arbitrary hosts. The threat model is *not* "untrusted internet user
controls the URL" - the URL comes from the local user typing it into the
UI - but we still apply defense-in-depth so a careless paste can't:

- read internal services (``http://localhost``, AWS metadata at
  ``http://169.254.169.254``, any RFC1918 range);
- exfiltrate data via large response bodies; or
- get pulled to a different host via a redirect after passing validation.

Defense-in-depth steps below map to the workspace
``codeguard-0-input-validation-injection`` and
``codeguard-0-api-web-services`` SSRF guidance.

Residual risk acknowledged but accepted for this local-only feature:
classic DNS-rebinding (host returns public IP at validation time, then
private IP when httpx connects). Mitigating that would require pinning
the resolved IP into the request transport, which is overkill here.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx
import trafilatura

from .schema import WebResult

log = logging.getLogger(__name__)


# Cap response body even if the server lies about Content-Length.
DEFAULT_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_CONTENT_CHARS = 8_000

_USER_AGENT = (
    "x-agent-research/0.1 (+https://github.com/local; "
    "respects robots.txt informally)"
)
_ALLOWED_SCHEMES = {"http", "https"}


class FetchError(RuntimeError):
    """Raised internally when a URL fails any safety check or HTTP request.

    Callers in ``compose`` swallow this and emit a logged warning so a
    single bad URL never breaks the rest of the research batch.
    """


def _ip_is_disallowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject anything pointing at internal infrastructure.

    Covers loopback (127.0.0.0/8, ::1), private ranges (10/8, 172.16/12,
    192.168/16, fc00::/7), link-local (169.254/16 incl. cloud metadata,
    fe80::/10), multicast, reserved, and the unspecified 0.0.0.0/:: which
    on Linux is treated as "all local interfaces" and routes to lo.
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_url(url: str) -> str:
    """Parse + scheme-check + DNS-resolve + IP-classify.

    Returns the validated URL string. Raises ``FetchError`` with a short
    reason on any failure. We resolve *every* address the host advertises
    (A and AAAA) and reject if ANY is disallowed - a permissive single-IP
    check would let a host with one public + one private IP slip through.
    """
    if not isinstance(url, str) or len(url) > 2048:
        raise FetchError("url must be a string under 2 KB")

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise FetchError(f"scheme not allowed: {parsed.scheme!r}")
    if not parsed.hostname:
        raise FetchError("missing hostname")

    host = parsed.hostname
    # Accept literal IP addresses too (some users paste those); validate
    # them directly without DNS.
    try:
        literal = ipaddress.ip_address(host)
        if _ip_is_disallowed(literal):
            raise FetchError(f"disallowed ip literal: {host}")
        return url
    except ValueError:
        pass  # not a literal IP, fall through to DNS

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"dns lookup failed: {exc}") from exc

    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for family, _socktype, _proto, _canon, sockaddr in infos:
        if family == socket.AF_INET:
            addrs.append(ipaddress.IPv4Address(sockaddr[0]))
        elif family == socket.AF_INET6:
            addrs.append(ipaddress.IPv6Address(sockaddr[0].split("%", 1)[0]))
    if not addrs:
        raise FetchError("dns returned no addresses")
    for addr in addrs:
        if _ip_is_disallowed(addr):
            raise FetchError(f"resolves to disallowed ip: {addr}")
    return url


def fetch_url(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    client: httpx.Client | None = None,
) -> WebResult:
    """Fetch ``url`` and return a populated :class:`WebResult`.

    Raises :class:`FetchError` on any failure. Callers in ``compose``
    catch it and log; they never let a single failure propagate to the
    HTTP layer above.
    """
    safe_url = _validate_url(url)

    owns_client = client is None
    if owns_client:
        client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,  # SSRF: never auto-follow to a new host
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
        )
    try:
        # ``stream`` so we can hard-cap bytes even if the server lies in
        # Content-Length. We don't bother with chunked decode niceties:
        # raw bytes -> trafilatura is fine.
        with client.stream("GET", safe_url) as resp:
            if 300 <= resp.status_code < 400:
                # Redirected: don't chase, but tell the user.
                raise FetchError(f"http {resp.status_code} redirect (not followed)")
            if resp.status_code >= 400:
                raise FetchError(f"http {resp.status_code}")

            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit():
                if int(content_length) > max_bytes:
                    raise FetchError(
                        f"response too large: content-length={content_length}"
                    )

            buf = bytearray()
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise FetchError(
                        f"response too large: streamed bytes={len(buf)}"
                    )
            body = bytes(buf)
    except httpx.HTTPError as exc:
        raise FetchError(f"http error: {type(exc).__name__}: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    # Title from <title> via a quick best-effort regex; we'll still get a
    # cleaner title from trafilatura if it's there.
    title = _extract_title(body) or ""

    try:
        extracted = trafilatura.extract(
            body,
            url=safe_url,
            no_fallback=False,
            favor_precision=True,
            include_comments=False,
            include_tables=True,
        )
    except Exception as exc:  # noqa: BLE001 - extractor library is best-effort
        log.warning("fetch_url: trafilatura failed for %s: %s", _host(safe_url), exc)
        extracted = None

    text = (extracted or "").strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "\u2026"

    log.info(
        "fetch_url host=%s status=200 bytes=%d chars=%d",
        _host(safe_url), len(body), len(text),
    )
    return WebResult(
        url=safe_url,
        title=title[:500],
        snippet=text[:300] if text else "",
        content=text,
        source="fetched",
    )


def _host(url: str) -> str:
    parsed = urlparse(url)
    return parsed.hostname or "?"


_TITLE_RE = None


def _extract_title(body: bytes) -> str | None:
    """Cheap <title> extractor; trafilatura also pulls one but doesn't always."""
    global _TITLE_RE
    if _TITLE_RE is None:
        import re
        _TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
    m = _TITLE_RE.search(body[:8192])  # title is in <head>; first 8 KB is plenty
    if not m:
        return None
    try:
        raw = m.group(1).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    # Collapse whitespace, strip HTML entities the lazy way.
    cleaned = " ".join(raw.split())
    return cleaned or None
