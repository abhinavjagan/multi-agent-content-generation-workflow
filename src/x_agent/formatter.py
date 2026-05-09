"""Pure formatting helpers: sanitization and thread splitting.

Kept free of LangGraph / Ollama / tweepy imports so it stays trivially
unit-testable.
"""

from __future__ import annotations

import re
import unicodedata


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RUN_RE = re.compile(r"[ \t]+")
# Sentence boundary: end punctuation followed by whitespace.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

MAX_TOPIC_CHARS = 280


def sanitize_topic(topic: str) -> str:
    """Validate and clean a user-supplied topic before sending it anywhere.

    - Strips control characters (prevents log/prompt injection of CR/LF).
    - Normalizes Unicode (NFC).
    - Collapses whitespace runs.
    - Enforces a hard length cap.

    Raises ``ValueError`` for empty or oversized input.
    """
    if topic is None:
        raise ValueError("topic must not be None")
    cleaned = unicodedata.normalize("NFC", topic)
    cleaned = _CONTROL_CHARS_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RUN_RE.sub(" ", cleaned).strip()
    if not cleaned:
        raise ValueError("topic must not be empty")
    if len(cleaned) > MAX_TOPIC_CHARS:
        raise ValueError(f"topic exceeds {MAX_TOPIC_CHARS} character limit")
    return cleaned


def _hard_chunk(text: str, limit: int) -> list[str]:
    """Last-resort splitter for runs longer than ``limit`` with no spaces.

    Splits on whitespace where possible; otherwise breaks the long word
    across multiple chunks rather than refusing to post.
    """
    out: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # Prefer to break at the last whitespace within the window.
        window = remaining[:limit]
        cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        out.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        out.append(remaining)
    return out


def split_into_thread(text: str, max_chars: int) -> list[str]:
    """Split ``text`` into tweet-sized chunks and prepend ``i/N`` numbering.

    The numbering prefix is included in the per-chunk length budget so every
    returned string is guaranteed to be ``<= max_chars``.

    Splitting prefers, in order:
        1. Paragraph boundaries (blank lines)
        2. Sentence boundaries
        3. Whitespace within an oversized sentence
        4. Hard character cuts (last resort)

    For a single resulting chunk, no numbering prefix is added.
    """
    if max_chars < 20:
        raise ValueError("max_chars too small to fit numbering prefix")

    if not text or not text.strip():
        return []

    # Worst-case prefix budget so we can finalize numbering after packing.
    # "999/999 " = 8 chars.
    worst_case_prefix = 8
    soft_limit = max_chars - worst_case_prefix

    # Step 1: split on blank lines into paragraphs, normalizing intra-paragraph whitespace.
    raw_paragraphs = re.split(r"\n\s*\n", text.strip())
    paragraphs: list[str] = []
    for p in raw_paragraphs:
        cleaned = re.sub(r"\s+", " ", p).strip()
        if cleaned:
            paragraphs.append(cleaned)

    # Step 2: break each paragraph that's too long into sentences (and then
    # hard-chunk any sentence that itself exceeds the limit).
    paragraph_blocks: list[list[str]] = []
    for p in paragraphs:
        if len(p) <= soft_limit:
            paragraph_blocks.append([p])
            continue
        sentences: list[str] = []
        for raw in _SENTENCE_SPLIT_RE.split(p):
            s = raw.strip()
            if not s:
                continue
            if len(s) <= soft_limit:
                sentences.append(s)
            else:
                sentences.extend(_hard_chunk(s, soft_limit))
        paragraph_blocks.append(sentences)

    # Step 3: pack pieces into chunks, never crossing a paragraph boundary.
    chunks: list[str] = []
    for sentences in paragraph_blocks:
        current = ""
        for s in sentences:
            candidate = s if not current else f"{current} {s}"
            if len(candidate) <= soft_limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = s
        if current:
            chunks.append(current)

    if not chunks:
        return []

    if len(chunks) == 1:
        # Final safety: ensure single tweet fits without numbering.
        only = chunks[0]
        if len(only) > max_chars:
            return _hard_chunk(only, max_chars)
        return [only]

    total = len(chunks)
    numbered: list[str] = []
    for i, body in enumerate(chunks, start=1):
        prefix = f"{i}/{total} "
        # If the actual prefix is shorter than the worst-case budget we used,
        # we may have headroom; if longer (shouldn't happen for total<1000),
        # trim the body.
        budget = max_chars - len(prefix)
        if len(body) > budget:
            body = body[: budget - 1].rstrip() + "\u2026"  # ellipsis
        numbered.append(prefix + body)

    # Final invariant check.
    for chunk in numbered:
        assert len(chunk) <= max_chars, (len(chunk), max_chars, chunk)
    return numbered


def trim_to_single(text: str, max_chars: int) -> str:
    """Trim ``text`` to fit a single tweet, preferring a clean word break."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= max_chars:
        return normalized
    cut = normalized.rfind(" ", 0, max_chars - 1)
    if cut <= 0:
        cut = max_chars - 1
    return normalized[:cut].rstrip() + "\u2026"
