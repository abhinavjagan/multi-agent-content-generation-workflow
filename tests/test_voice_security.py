"""Tests for upload validation + rate limiting on /api/voice/* endpoints."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from x_agent.voice.security import (
    RateLimiter,
    client_key,
    validate_audio_upload,
)


# ----------------------------------------------------------- magic-byte sniff


def _webm_bytes() -> bytes:
    # EBML header followed by some padding. Real webm files have much
    # more after this, but our sniffer only checks the first 4 bytes.
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def _ogg_bytes() -> bytes:
    return b"OggS" + b"\x00" * 64


def _wav_bytes() -> bytes:
    # RIFF<size>WAVEfmt …
    return b"RIFF\x10\x00\x00\x00WAVEfmt " + b"\x00" * 64


def _mp4_bytes() -> bytes:
    # 4-byte box size, then 'ftyp', then minor brand etc.
    return b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64


def _mp3_bytes() -> bytes:
    return b"ID3\x03\x00" + b"\x00" * 64


class TestValidateAudioUpload:
    def test_accepts_webm(self):
        ext = validate_audio_upload(
            content_type="audio/webm;codecs=opus",
            body=_webm_bytes(),
            max_bytes=1_000_000,
        )
        assert ext == ".webm"

    def test_accepts_ogg(self):
        assert (
            validate_audio_upload(
                content_type="audio/ogg",
                body=_ogg_bytes(),
                max_bytes=1_000_000,
            )
            == ".ogg"
        )

    def test_accepts_wav_lowercase(self):
        assert (
            validate_audio_upload(
                content_type="AUDIO/WAV",
                body=_wav_bytes(),
                max_bytes=1_000_000,
            )
            == ".wav"
        )

    def test_accepts_mp4(self):
        assert (
            validate_audio_upload(
                content_type="audio/mp4",
                body=_mp4_bytes(),
                max_bytes=1_000_000,
            )
            == ".mp4"
        )

    def test_accepts_mp3(self):
        assert (
            validate_audio_upload(
                content_type="audio/mpeg",
                body=_mp3_bytes(),
                max_bytes=1_000_000,
            )
            == ".mp3"
        )

    def test_rejects_size_cap(self):
        with pytest.raises(HTTPException) as exc:
            validate_audio_upload(
                content_type="audio/webm",
                body=_webm_bytes() + b"\x00" * 2000,
                max_bytes=1000,
            )
        assert exc.value.status_code == 413

    def test_rejects_empty(self):
        with pytest.raises(HTTPException) as exc:
            validate_audio_upload(
                content_type="audio/webm",
                body=b"",
                max_bytes=1_000_000,
            )
        assert exc.value.status_code == 422

    def test_rejects_unknown_type(self):
        with pytest.raises(HTTPException) as exc:
            validate_audio_upload(
                content_type="text/plain",
                body=b"hello",
                max_bytes=1_000_000,
            )
        assert exc.value.status_code == 415

    def test_rejects_missing_type(self):
        with pytest.raises(HTTPException) as exc:
            validate_audio_upload(
                content_type=None,
                body=_webm_bytes(),
                max_bytes=1_000_000,
            )
        assert exc.value.status_code == 415

    def test_rejects_magic_mismatch(self):
        """Declaring audio/wav but sending webm bytes -> reject."""
        with pytest.raises(HTTPException) as exc:
            validate_audio_upload(
                content_type="audio/wav",
                body=_webm_bytes(),
                max_bytes=1_000_000,
            )
        assert exc.value.status_code == 415

    def test_rejects_html_disguised_as_audio(self):
        """A common injection pattern: HTML masquerading as audio."""
        with pytest.raises(HTTPException) as exc:
            validate_audio_upload(
                content_type="audio/webm",
                body=b"<!doctype html><html>...</html>",
                max_bytes=1_000_000,
            )
        assert exc.value.status_code == 415


# ----------------------------------------------------------------- rate limit


class TestRateLimiter:
    def test_allows_within_window(self):
        rl = RateLimiter(max_per_window=3, window_seconds=60)
        assert rl.allow("client-a") == (True, 0.0)
        assert rl.allow("client-a") == (True, 0.0)
        assert rl.allow("client-a") == (True, 0.0)

    def test_denies_after_quota(self):
        rl = RateLimiter(max_per_window=2, window_seconds=60)
        rl.allow("a")
        rl.allow("a")
        allowed, retry = rl.allow("a")
        assert allowed is False
        assert retry > 0

    def test_isolated_keys(self):
        rl = RateLimiter(max_per_window=1, window_seconds=60)
        rl.allow("a")
        # Different key still allowed.
        allowed, _ = rl.allow("b")
        assert allowed is True
        # Original key now blocked.
        allowed, _ = rl.allow("a")
        assert allowed is False

    def test_window_slides(self, monkeypatch):
        """Once tokens expire from the sliding window they free up again."""
        import x_agent.voice.security as sec

        clock = {"t": 1000.0}

        def fake_monotonic():
            return clock["t"]

        monkeypatch.setattr(sec.time, "monotonic", fake_monotonic)
        rl = RateLimiter(max_per_window=2, window_seconds=10)
        assert rl.allow("a")[0] is True
        assert rl.allow("a")[0] is True
        assert rl.allow("a")[0] is False
        clock["t"] += 11  # both prior tokens fall off
        assert rl.allow("a")[0] is True


def test_client_key_fallbacks():
    assert client_key("127.0.0.1") == "127.0.0.1"
    assert client_key("") == "unknown"
    assert client_key(None) == "unknown"
