# Web research

x-agent stays local-only **until you opt in**. The `research` node is a no-op by default. Turn it on per-request from the UI (a toggle on the Draft page) or the CLI (`--research`, `--url`, `--query`).

When enabled, the agent does exactly **one** of two things:

1. **URL mode (preferred).** You pass one or more `--url`s (CLI) or fill the *URLs to summarize* textarea in the UI. The agent fetches each, extracts article text via `trafilatura`, and feeds that as grounding. Search is skipped.
2. **Topic-driven search.** With no URLs provided, the agent searches the configured provider for `--query` (defaults to the topic) and fetches the top-k results.

The extracted text becomes a `WEB CONTEXT` block in the generation prompt. The writer is instructed to use it as inspiration, *not* to quote it verbatim.

## Provider selection

`RESEARCH_PROVIDER` controls who answers your query.

| Provider | Required env var | Notes |
| --- | --- | --- |
| Tavily | `TAVILY_API_KEY` | AI-tuned snippets, free 1000/mo |
| Brave Search | `BRAVE_SEARCH_API_KEY` | free 2000/mo at 1 qps |
| DuckDuckGo | (none) | default fallback; no key needed |

With `RESEARCH_PROVIDER=auto` (the default), x-agent picks the best one given your env. The chosen provider name is surfaced on `/api/health` so you always know who your query is going to before you click Generate.

## CLI examples

```bash
# search-only (DuckDuckGo by default)
x-agent generate "what makes a good engineering culture" --research

# fetch a specific URL the user already chose
x-agent generate "what's new in postgres 18" \
    --url https://www.postgresql.org/about/news/postgresql-18-released-3142/

# override the search query without changing the topic
x-agent generate "engineering culture" --query "founder mode vs manager mode"
```

The same fields are exposed on `POST /api/draft` (`research_enabled`, `research_urls`, `research_query`). `POST /api/research/preview` returns just the source list (titles + URLs + snippets) without invoking the LLM — the UI uses it to show what was found before generation starts. Source cards are rendered under the review screen.

## Hardening

The research subsystem treats every URL as hostile until proven otherwise. From `src/x_agent/research/`:

- **SSRF guards.** Scheme allow-list (`http`, `https` only — no `file://`, no `gopher://`, etc.). DNS resolution happens *before* the request, and the resolved IP is checked against `is_private | is_loopback | is_link_local | is_reserved | is_multicast`. Redirects are disabled so a 302 can't escape into the private network.
- **DoS guards.** 10 s per-request HTTP timeout (configurable), 1 MB per-response cap, ~12 s overall wall-clock cap, max 4 concurrent fetches.
- **Privacy.** API keys are stored as `pydantic.SecretStr` and only surfaced in `/api/health` as booleans (`tavily: { ok: true }`). The provider name is shown so you can audit who's seeing your query. URLs and search queries are logged at INFO; their *contents* and *responses* are not.

If you're worried about leaking topic ideas to a search provider, leave research off and use URL mode for the cases where you already have a source in mind.

## What can go wrong (and what the UI shows)

| Failure | What you'll see |
| --- | --- |
| URL blocked (private IP) | toast: *URL refused — points at a private/reserved address* |
| URL exceeds size cap | toast: *Response capped at 1 MB; try a different source* |
| Search provider returns nothing | empty source list, the draft proceeds without grounding |
| Provider API key invalid | provider falls back to DuckDuckGo automatically; `/api/health` shows the bad provider as `ok: false` |
