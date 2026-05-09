"""Unit tests for the formatter module."""

from __future__ import annotations

import pytest

from x_agent.formatter import (
    MAX_TOPIC_CHARS,
    sanitize_topic,
    split_into_thread,
    trim_to_single,
)


class TestSanitizeTopic:
    def test_strips_control_chars(self) -> None:
        out = sanitize_topic("hello\x00\x07world\r\n")
        assert "\x00" not in out
        assert "\x07" not in out
        assert out == "hello world"

    def test_collapses_whitespace(self) -> None:
        assert sanitize_topic("foo    bar\t\tbaz") == "foo bar baz"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            sanitize_topic("")
        with pytest.raises(ValueError):
            sanitize_topic("   \t  ")

    def test_rejects_oversized(self) -> None:
        with pytest.raises(ValueError):
            sanitize_topic("x" * (MAX_TOPIC_CHARS + 1))

    def test_rejects_none(self) -> None:
        with pytest.raises(ValueError):
            sanitize_topic(None)  # type: ignore[arg-type]


class TestSplitIntoThread:
    def test_short_text_single_chunk_no_numbering(self) -> None:
        chunks = split_into_thread("Short post.", max_chars=275)
        assert chunks == ["Short post."]

    def test_returns_empty_for_empty_input(self) -> None:
        assert split_into_thread("", max_chars=275) == []
        assert split_into_thread("   \n\t", max_chars=275) == []

    def test_multi_sentence_packs_then_numbers(self) -> None:
        text = " ".join([f"Sentence number {i}." for i in range(1, 30)])
        chunks = split_into_thread(text, max_chars=100)
        assert len(chunks) >= 2
        # Every chunk has i/N prefix and respects the limit.
        for i, chunk in enumerate(chunks, start=1):
            assert chunk.startswith(f"{i}/{len(chunks)} ")
            assert len(chunk) <= 100

    def test_respects_max_chars_invariant(self) -> None:
        long = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 50).strip()
        for limit in (60, 100, 200, 275):
            chunks = split_into_thread(long, max_chars=limit)
            assert chunks, "should produce at least one chunk"
            for c in chunks:
                assert len(c) <= limit, (limit, len(c), c)

    def test_handles_unbroken_long_word(self) -> None:
        # A single token longer than the soft limit must be hard-chunked.
        chunks = split_into_thread("a" * 800, max_chars=100)
        assert all(len(c) <= 100 for c in chunks)
        # All numbered if more than one chunk.
        if len(chunks) > 1:
            assert chunks[0].startswith("1/")

    def test_too_small_max_chars_raises(self) -> None:
        with pytest.raises(ValueError):
            split_into_thread("hello world", max_chars=10)

    def test_numbering_prefix_consistency(self) -> None:
        text = " ".join([f"Point {i} here." for i in range(1, 20)])
        chunks = split_into_thread(text, max_chars=80)
        n = len(chunks)
        for i, chunk in enumerate(chunks, start=1):
            assert chunk.startswith(f"{i}/{n} ")

    def test_does_not_cross_paragraph_boundary(self) -> None:
        # Each short paragraph easily fits in one tweet on its own; if they
        # were packed across the blank-line boundary the result would be one
        # tweet, but paragraph awareness must keep them separate.
        text = "Para one is here.\n\nPara two is over here."
        chunks = split_into_thread(text, max_chars=275)
        assert len(chunks) == 2
        assert chunks[0].endswith("Para one is here.")
        assert chunks[1].endswith("Para two is over here.")

    def test_packs_within_paragraph(self) -> None:
        # Multiple sentences within one paragraph SHOULD be packed together
        # if they fit, while two paragraphs stay split.
        para = "Short one. Short two. Short three."
        text = f"{para}\n\n{para}"
        chunks = split_into_thread(text, max_chars=275)
        assert len(chunks) == 2  # one chunk per paragraph
        for c in chunks:
            assert "Short one. Short two. Short three." in c


class TestTrimToSingle:
    def test_short_text_unchanged(self) -> None:
        assert trim_to_single("hi", max_chars=275) == "hi"

    def test_collapses_whitespace_first(self) -> None:
        assert trim_to_single("a  b   c", max_chars=275) == "a b c"

    def test_truncates_with_ellipsis(self) -> None:
        text = " ".join(["word"] * 200)
        out = trim_to_single(text, max_chars=50)
        assert len(out) <= 50
        assert out.endswith("\u2026")

    def test_prefers_word_boundary(self) -> None:
        text = "alpha beta gamma delta epsilon zeta eta theta"
        out = trim_to_single(text, max_chars=20)
        # Should not split mid-word; the part before ellipsis is whole words.
        body = out.rstrip("\u2026").strip()
        for tok in body.split(" "):
            assert tok in text.split(" ")
