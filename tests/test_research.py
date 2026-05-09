"""Tests for the web research subsystem.

We never make a real network call here:

- ``respx`` intercepts every ``httpx`` request the providers and fetcher
  would make.
- For SSRF tests we use literal IP addresses so DNS isn't involved.
- For the DNS-resolution path we monkeypatch ``socket.getaddrinfo``.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from x_agent.research import (
    BaseProvider,
    BraveProvider,
    DuckDuckGoProvider,
    TavilyProvider,
    WebResult,
    gather_research,
    provider_name,
    select_provider,
)
from x_agent.research.compose import _canonical_url, _clean_urls
from x_agent.research.fetcher import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_CONTENT_CHARS,
    FetchError,
    _validate_url,
    fetch_url,
)


# ----------------------------------------------------------------- _validate_url


class TestValidateUrlSchemes:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com/file",
            "file:///etc/passwd",
            "gopher://example.com/",
            "javascript:alert(1)",
            "data:text/html,<h1>x</h1>",
            "ws://example.com/",
        ],
    )
    def test_blocks_non_http_schemes(self, url: str) -> None:
        with pytest.raises(FetchError, match="scheme"):
            _validate_url(url)

    def test_accepts_https(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
        assert _validate_url("https://example.com/page").startswith("https://")

    def test_accepts_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
        assert _validate_url("http://example.com/page").startswith("http://")


class TestValidateUrlSsrf:
    """Block all the classic SSRF targets even when given as IP literals."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://127.5.5.5/",
            "http://10.0.0.5/",
            "http://172.16.0.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",  # AWS metadata
            "http://0.0.0.0/",
            "http://[::1]/",
            "http://[fc00::1]/",
            "http://[fe80::1]/",
        ],
    )
    def test_rejects_disallowed_ip_literals(self, url: str) -> None:
        with pytest.raises(FetchError, match="(disallowed|unspecified|loopback|private|reserved|link)"):
            _validate_url(url)

    def test_rejects_private_dns_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.5"))
        with pytest.raises(FetchError, match="disallowed"):
            _validate_url("http://internal.corp/")

    def test_rejects_when_any_resolved_ip_is_private(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Host advertises one public + one private IP -- still reject.
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34", "10.0.0.5")
        )
        with pytest.raises(FetchError, match="disallowed"):
            _validate_url("http://mixed.example/")

    def test_rejects_dns_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: Any, **kwargs: Any) -> Any:
            raise socket.gaierror(8, "no such host")

        monkeypatch.setattr(socket, "getaddrinfo", boom)
        with pytest.raises(FetchError, match="dns"):
            _validate_url("http://nope.invalid/")

    def test_rejects_oversized_url(self) -> None:
        long_url = "https://example.com/" + ("a" * 2100)
        with pytest.raises(FetchError, match="2 KB"):
            _validate_url(long_url)


def _fake_getaddrinfo(*ips: str) -> Any:
    """Return a fake ``socket.getaddrinfo`` that yields the given IPs."""

    def _impl(_host: str, _port: Any, *_args: Any, **_kw: Any) -> list[Any]:
        infos = []
        for ip in ips:
            if ":" in ip:
                infos.append((socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, 0, 0, 0)))
            else:
                infos.append((socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)))
        return infos

    return _impl


# ----------------------------------------------------------------- fetch_url


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``socket.getaddrinfo`` return a known-public IP for any host."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))


@pytest.fixture
def article_html() -> bytes:
    return (
        b"<html><head><title>Hello World</title></head>"
        b"<body><article><h1>Hello</h1>"
        b"<p>This is a substantive article paragraph with enough text "
        b"for trafilatura to keep it. We talk about engineering culture "
        b"and how teams ship reliably under pressure.</p>"
        b"<p>A second paragraph reinforces the topic so the extractor "
        b"is confident and returns the body.</p></article></body></html>"
    )


class TestFetchUrl:
    @respx.mock
    def test_happy_path(self, public_dns: None, article_html: bytes) -> None:
        respx.get("https://example.com/post").mock(
            return_value=httpx.Response(200, content=article_html)
        )
        result = fetch_url("https://example.com/post")
        assert isinstance(result, WebResult)
        assert result.source == "fetched"
        assert "Hello" in result.title
        assert "engineering" in result.content

    @respx.mock
    def test_truncates_long_content(
        self, public_dns: None
    ) -> None:
        big_text = "lorem ipsum " * 5000
        body = (
            b"<html><body><article>"
            + b"<p>" + big_text.encode() + b"</p>"
            + b"</article></body></html>"
        )
        respx.get("https://example.com/big").mock(
            return_value=httpx.Response(200, content=body)
        )
        result = fetch_url("https://example.com/big", max_chars=200)
        assert len(result.content) <= 200

    @respx.mock
    def test_aborts_on_oversized_content_length(self, public_dns: None) -> None:
        respx.get("https://example.com/huge").mock(
            return_value=httpx.Response(
                200,
                content=b"<html></html>",
                headers={"content-length": str(DEFAULT_MAX_BYTES * 10)},
            )
        )
        with pytest.raises(FetchError, match="too large"):
            fetch_url("https://example.com/huge")

    @respx.mock
    def test_aborts_on_streamed_overflow(self, public_dns: None) -> None:
        # 2 MB of zeros, no content-length header -> streaming check kicks in.
        big = b"a" * (DEFAULT_MAX_BYTES * 2)
        respx.get("https://example.com/stream").mock(
            return_value=httpx.Response(200, content=big)
        )
        with pytest.raises(FetchError, match="too large"):
            fetch_url("https://example.com/stream", max_bytes=512)

    @respx.mock
    def test_4xx_propagates_as_fetch_error(self, public_dns: None) -> None:
        respx.get("https://example.com/missing").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(FetchError, match="404"):
            fetch_url("https://example.com/missing")

    @respx.mock
    def test_redirect_not_followed(self, public_dns: None) -> None:
        respx.get("https://example.com/old").mock(
            return_value=httpx.Response(302, headers={"location": "https://evil.com/"})
        )
        with pytest.raises(FetchError, match="redirect"):
            fetch_url("https://example.com/old")

    @respx.mock
    def test_ssrf_runs_before_http(self) -> None:
        route = respx.get("http://10.0.0.5/").mock(
            return_value=httpx.Response(200, content=b"<html></html>")
        )
        with pytest.raises(FetchError, match="disallowed"):
            fetch_url("http://10.0.0.5/")
        assert route.called is False  # request must never have happened


# ----------------------------------------------------------------- compose


class _StubProvider(BaseProvider):
    """Returns a fixed list of search hits with snippet content."""

    name = "stub"

    def __init__(self, results: list[WebResult]) -> None:
        self._results = results

    def search(self, query: str, *, k: int) -> list[WebResult]:
        return self._results[:k]


def _stub_fetch_factory(text_by_url: dict[str, str], *, fail: set[str] | None = None) -> Any:
    fail = fail or set()

    def _fetch(url: str, *, timeout: float, max_bytes: int, max_chars: int) -> WebResult:
        if url in fail:
            raise FetchError("simulated failure")
        text = text_by_url.get(url, f"text for {url}")
        return WebResult(
            url=url,
            title=f"Title for {url}",
            snippet=text[:200],
            content=text[:max_chars],
            source="fetched",
        )

    return _fetch


def _settings(**overrides: Any) -> Any:
    base = {
        "research_provider": "auto",
        "tavily_api_key": None,
        "brave_search_api_key": None,
        "research_max_results": 4,
        "research_fetch_timeout_s": 10.0,
        "research_max_content_chars": DEFAULT_MAX_CONTENT_CHARS,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestComposeUrlMode:
    def test_url_mode_skips_search(self) -> None:
        # Provider raises if called -- we shouldn't reach it.
        class _Bomb(BaseProvider):
            name = "bomb"
            def search(self, query: str, *, k: int) -> list[WebResult]:
                raise AssertionError("search should not run when urls are given")

        results = gather_research(
            query="ignored",
            urls=["https://a.example/x", "https://b.example/y"],
            k=4,
            settings=_settings(),
            provider=_Bomb(),
            fetch_fn=_stub_fetch_factory(
                {"https://a.example/x": "alpha", "https://b.example/y": "beta"}
            ),
        )
        assert [str(r.url) for r in results] == [
            "https://a.example/x",
            "https://b.example/y",
        ]
        assert all(r.source == "fetched" for r in results)

    def test_url_mode_dedupes_and_caps(self) -> None:
        urls = [
            "https://x.example/a",
            "https://x.example/a/",  # trailing slash dupe
            "https://X.Example/a",   # case dupe
            "https://x.example/b",
            "https://x.example/c",
            "https://x.example/d",
            "https://x.example/e",
            "https://x.example/f",  # cap at 5
        ]
        results = gather_research(
            query=None,
            urls=urls,
            k=10,
            settings=_settings(),
            fetch_fn=_stub_fetch_factory({}),
        )
        # _clean_urls caps at 5, gather_research caps at k=10.
        assert len(results) == 5

    def test_url_mode_logs_failures_but_returns_others(self) -> None:
        results = gather_research(
            query=None,
            urls=["https://ok.example/", "https://bad.example/"],
            k=4,
            settings=_settings(),
            fetch_fn=_stub_fetch_factory(
                {"https://ok.example/": "good"},
                fail={"https://bad.example/"},
            ),
        )
        urls = {str(r.url) for r in results}
        assert "https://ok.example/" in urls
        assert "https://bad.example/" not in urls


class TestComposeSearchMode:
    def test_search_mode_merges_fetched_content(self) -> None:
        provider = _StubProvider([
            WebResult(
                url="https://hit.example/1",
                title="Hit 1",
                snippet="provider snippet",
                content="",
                source="search",
                provider="stub",
            ),
        ])
        results = gather_research(
            query="topic",
            urls=None,
            k=4,
            settings=_settings(),
            provider=provider,
            fetch_fn=_stub_fetch_factory({"https://hit.example/1": "fetched body text"}),
        )
        assert len(results) == 1
        # Hit kept its provider snippet AND got fetched content folded in.
        assert results[0].snippet == "provider snippet"
        assert "fetched body" in results[0].content
        assert results[0].source == "search"

    def test_search_mode_keeps_hit_when_fetch_fails(self) -> None:
        provider = _StubProvider([
            WebResult(
                url="https://nofetch.example/",
                title="Snippet only",
                snippet="snippet text",
                content="",
                source="search",
                provider="stub",
            ),
        ])
        results = gather_research(
            query="topic",
            urls=None,
            k=4,
            settings=_settings(),
            provider=provider,
            fetch_fn=_stub_fetch_factory(
                {}, fail={"https://nofetch.example/"}
            ),
        )
        assert len(results) == 1
        assert results[0].snippet == "snippet text"
        assert results[0].content == ""

    def test_empty_query_returns_empty_list(self) -> None:
        assert gather_research(query="", urls=None, k=4, settings=_settings()) == []
        assert gather_research(query=None, urls=None, k=4, settings=_settings()) == []


# ----------------------------------------------------------------- providers


class TestSelectProvider:
    def test_default_is_duckduckgo(self) -> None:
        prov = select_provider(_settings())
        assert prov.name == "duckduckgo"

    def test_picks_tavily_when_key_present(self) -> None:
        from pydantic import SecretStr
        prov = select_provider(_settings(tavily_api_key=SecretStr("tvly-test")))
        assert prov.name == "tavily"

    def test_picks_brave_when_key_present(self) -> None:
        from pydantic import SecretStr
        prov = select_provider(_settings(brave_search_api_key=SecretStr("brave-test")))
        assert prov.name == "brave"

    def test_explicit_tavily_falls_back_when_key_missing(self) -> None:
        prov = select_provider(_settings(research_provider="tavily"))
        assert prov.name == "duckduckgo"

    def test_provider_name_helper(self) -> None:
        assert provider_name(_settings()) == "duckduckgo"


class TestTavilyProvider:
    @respx.mock
    def test_search_parses_results(self) -> None:
        respx.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://blog.example/x",
                            "title": "X",
                            "content": "snippet x",
                            "score": 0.91,
                        },
                        {
                            "url": "https://blog.example/y",
                            "title": "Y",
                            "content": "snippet y",
                            "score": 0.42,
                        },
                    ]
                },
            )
        )
        prov = TavilyProvider("tvly-fake")
        out = prov.search("good engineering culture", k=2)
        assert [str(r.url) for r in out] == [
            "https://blog.example/x",
            "https://blog.example/y",
        ]
        assert out[0].score == 0.91
        assert out[0].provider == "tavily"
        assert out[0].source == "search"

    @respx.mock
    def test_search_returns_empty_on_http_error(self) -> None:
        respx.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        assert TavilyProvider("tvly-fake").search("x", k=2) == []

    @respx.mock
    def test_search_returns_empty_on_network_error(self) -> None:
        respx.post("https://api.tavily.com/search").mock(
            side_effect=httpx.ConnectError("network down")
        )
        assert TavilyProvider("tvly-fake").search("x", k=2) == []

    def test_rejects_empty_key(self) -> None:
        with pytest.raises(ValueError):
            TavilyProvider("")


class TestBraveProvider:
    @respx.mock
    def test_search_parses_results(self) -> None:
        respx.get(
            "https://api.search.brave.com/res/v1/web/search",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "url": "https://blog.example/a",
                                "title": "A",
                                "description": "desc a",
                            },
                            {
                                "url": "https://blog.example/b",
                                "title": "B",
                                "description": "desc b",
                            },
                        ]
                    }
                },
            )
        )
        out = BraveProvider("brave-fake").search("query", k=2)
        assert [str(r.url) for r in out] == [
            "https://blog.example/a",
            "https://blog.example/b",
        ]
        assert out[0].provider == "brave"

    @respx.mock
    def test_search_handles_garbage_payload(self) -> None:
        respx.get(
            "https://api.search.brave.com/res/v1/web/search",
        ).mock(return_value=httpx.Response(200, json={"unexpected": True}))
        assert BraveProvider("brave-fake").search("x", k=2) == []


class TestDuckDuckGoProvider:
    def test_handles_library_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate ``ddgs`` raising mid-search.
        import ddgs as ddgs_mod

        class _FailingDDGS:
            def text(self, *_args: Any, **_kw: Any) -> Any:
                raise RuntimeError("ratelimited")

        monkeypatch.setattr(ddgs_mod, "DDGS", lambda: _FailingDDGS())
        assert DuckDuckGoProvider().search("x", k=2) == []

    def test_parses_library_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ddgs as ddgs_mod

        class _StubDDGS:
            def text(self, query: str, **_kw: Any) -> list[dict[str, Any]]:
                return [
                    {"title": "T", "href": "https://x.example/", "body": "snippet"},
                    {"title": "T2", "href": "https://y.example/", "body": "s2"},
                ]

        monkeypatch.setattr(ddgs_mod, "DDGS", lambda: _StubDDGS())
        out = DuckDuckGoProvider().search("any", k=2)
        assert len(out) == 2
        assert out[0].provider == "duckduckgo"


# ----------------------------------------------------------------- helpers


class TestCanonicalUrl:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("https://EXAMPLE.com/a", "https://example.com/a"),
            ("https://example.com/a/", "https://example.com/a"),
            ("https://example.com/a", "HTTPS://example.com/a"),
        ],
    )
    def test_treats_variants_as_equal(self, a: str, b: str) -> None:
        assert _canonical_url(a) == _canonical_url(b)

    def test_keeps_query_string(self) -> None:
        assert _canonical_url("https://x.com/a?b=1") != _canonical_url("https://x.com/a")


class TestCleanUrls:
    def test_dedupes_preserves_order(self) -> None:
        out = _clean_urls([
            "https://a.example/", "https://a.example/", "https://b.example/",
        ])
        assert out == ["https://a.example/", "https://b.example/"]

    def test_caps_at_five(self) -> None:
        out = _clean_urls([f"https://{i}.example/" for i in range(10)])
        assert len(out) == 5

    def test_strips_and_skips_empties(self) -> None:
        assert _clean_urls(["", "   ", "https://x/"]) == ["https://x/"]

    def test_handles_none(self) -> None:
        assert _clean_urls(None) == []


# ----------------------------------------------------------------- node + prompt


class TestResearchNode:
    """The graph node wrapping ``gather_research``.

    We patch ``gather_research`` at the module boundary so this stays a
    unit test (no provider call, no httpx, no respx).
    """

    def test_no_op_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from x_agent import nodes

        called = {"n": 0}

        def fake_gather(**_kw: Any) -> list[WebResult]:
            called["n"] += 1
            return []

        monkeypatch.setattr(nodes, "gather_research", fake_gather)
        out = nodes.research({"research_enabled": False, "topic": "x"})
        assert out == {"web_results": []}
        assert called["n"] == 0  # never even called

    def test_calls_gather_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from x_agent import nodes

        def fake_gather(**_kw: Any) -> list[WebResult]:
            return [
                WebResult(
                    url="https://x.example/",
                    title="t",
                    snippet="s",
                    content="c",
                    source="search",
                    provider="stub",
                ),
            ]

        monkeypatch.setattr(nodes, "gather_research", fake_gather)
        out = nodes.research({
            "research_enabled": True,
            "topic": "good engineering",
            "research_urls": [],
        })
        assert "web_results" in out
        assert len(out["web_results"]) == 1
        # Must be JSON-serialisable dicts, not Pydantic objects.
        assert isinstance(out["web_results"][0], dict)
        assert out["web_results"][0]["url"] == "https://x.example/"

    def test_swallows_unexpected_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from x_agent import nodes

        def boom(**_kw: Any) -> list[WebResult]:
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(nodes, "gather_research", boom)
        out = nodes.research({"research_enabled": True, "topic": "x"})
        # Failure must NEVER break the graph -- empty list, log a warning.
        assert out == {"web_results": []}


class TestWebContextBlock:
    def test_empty_returns_empty(self) -> None:
        from x_agent.nodes import _web_context_block
        assert _web_context_block(None) == ""
        assert _web_context_block([]) == ""

    def test_renders_numbered_sources(self) -> None:
        from x_agent.nodes import _web_context_block

        block = _web_context_block([
            WebResult(
                url="https://a.example/post",
                title="Alpha post",
                snippet="alpha snippet body",
                content="alpha full content body",
                source="fetched",
            ).model_dump(mode="json"),
            WebResult(
                url="https://b.example/x",
                title="Bravo",
                snippet="bravo snippet",
                content="",
                source="search",
            ).model_dump(mode="json"),
        ])
        assert "WEB CONTEXT" in block
        assert "[1]" in block
        assert "[2]" in block
        assert "Alpha post" in block
        assert "a.example" in block
        assert "Bravo" in block

    def test_skips_sources_with_no_text(self) -> None:
        from x_agent.nodes import _web_context_block
        block = _web_context_block([
            WebResult(
                url="https://empty.example/",
                title="",
                snippet="",
                content="",
                source="fetched",
            ).model_dump(mode="json"),
        ])
        assert block == ""
