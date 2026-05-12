"""Tests for the FastAPI voice endpoints with mocked engines.

The real engines pull weights from the internet, so these tests stub
:class:`KokoroTTS` and :class:`WhisperSTT` with deterministic fakes and
exercise the HTTP surface (status codes, headers, content types, size
caps, content-type allowlist, rate limit).
"""

from __future__ import annotations

import wave
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from x_agent import server as srv
from x_agent.voice import KokoroTTS, VoiceEngineError, WhisperSTT
from x_agent.voice.security import RateLimiter


# --------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _reset_voice_limiter():
    # Each test gets a fresh, generous limiter so unrelated tests don't
    # pollute each other's bucket.
    srv._voice_rate_limiter = RateLimiter(max_per_window=1000, window_seconds=60)
    yield
    srv._voice_rate_limiter = None


@pytest.fixture()
def client():
    return TestClient(srv.app)


def _wav_bytes(seconds: float = 0.5, sample_rate: int = 16000) -> bytes:
    """Generate a tiny but valid WAV blob for upload tests."""
    n = int(sample_rate * seconds)
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


def _webm_bytes() -> bytes:
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 256


# ----------------------------------------------------------------- /api/voice/speak


def test_speak_returns_wav(client, monkeypatch):
    sample = b"RIFF\x10\x00\x00\x00WAVEfake-audio"

    def fake_synth(self, text, *, voice=None, speed=None, lang=None):
        assert text.strip() == "hello world"
        return sample

    monkeypatch.setattr(KokoroTTS, "synthesize", fake_synth, raising=False)
    res = client.post("/api/voice/speak", json={"text": "hello world"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.content == sample


def test_speak_text_too_long(client, monkeypatch):
    # Pydantic ceiling is 2000, server enforces runtime tts_max_chars (800
    # default). Send a 1500-char string that bypasses Pydantic but trips
    # the server's runtime cap.
    monkeypatch.setattr(
        KokoroTTS, "synthesize",
        lambda self, text, **kw: b"unused",
        raising=False,
    )
    res = client.post("/api/voice/speak", json={"text": "a" * 1500})
    assert res.status_code == 413


def test_speak_rejects_empty(client):
    res = client.post("/api/voice/speak", json={"text": ""})
    assert res.status_code == 422  # pydantic min_length=1


def test_speak_503_when_engine_unavailable(client, monkeypatch):
    from x_agent.voice import VoiceEngineUnavailable

    def explode(self, text, **kw):
        raise VoiceEngineUnavailable("model missing")

    monkeypatch.setattr(KokoroTTS, "synthesize", explode, raising=False)
    res = client.post("/api/voice/speak", json={"text": "hi"})
    assert res.status_code == 503
    assert "TTS engine unavailable" in res.json()["detail"]


def test_speak_400_on_engine_error(client, monkeypatch):
    def explode(self, text, **kw):
        raise VoiceEngineError("bad voice")

    monkeypatch.setattr(KokoroTTS, "synthesize", explode, raising=False)
    res = client.post("/api/voice/speak", json={"text": "hi"})
    assert res.status_code == 400


# ------------------------------------------------------------- /api/voice/transcribe


def test_transcribe_happy_wav(client, monkeypatch):
    def fake_transcribe(self, data, *, suffix):
        assert suffix == ".wav"
        return ("hello there", 0.5)

    monkeypatch.setattr(WhisperSTT, "transcribe", fake_transcribe, raising=False)
    files = {"audio": ("answer.wav", _wav_bytes(), "audio/wav")}
    res = client.post("/api/voice/transcribe", files=files)
    assert res.status_code == 200
    body = res.json()
    assert body["text"] == "hello there"
    assert body["duration_s"] == pytest.approx(0.5)
    assert "model" in body


def test_transcribe_happy_webm(client, monkeypatch):
    def fake_transcribe(self, data, *, suffix):
        assert suffix == ".webm"
        return ("voice memo", 1.25)

    monkeypatch.setattr(WhisperSTT, "transcribe", fake_transcribe, raising=False)
    files = {"audio": ("a.webm", _webm_bytes(), "audio/webm")}
    res = client.post("/api/voice/transcribe", files=files)
    assert res.status_code == 200


def test_transcribe_oversize_rejected(client, monkeypatch):
    # Set a tiny cap so a routine WAV exceeds it.
    monkeypatch.setattr(
        srv.get_settings(), "voice_max_audio_bytes", 100, raising=False
    )
    files = {"audio": ("a.wav", _wav_bytes(), "audio/wav")}
    res = client.post("/api/voice/transcribe", files=files)
    assert res.status_code == 413


def test_transcribe_bad_content_type(client, monkeypatch):
    monkeypatch.setattr(
        WhisperSTT,
        "transcribe",
        lambda self, data, *, suffix: ("x", 0.1),
        raising=False,
    )
    files = {"audio": ("a.txt", b"not audio", "text/plain")}
    res = client.post("/api/voice/transcribe", files=files)
    assert res.status_code == 415


def test_transcribe_magic_mismatch(client, monkeypatch):
    """Says audio/wav but sends webm bytes -> 415, not 200."""
    monkeypatch.setattr(
        WhisperSTT,
        "transcribe",
        lambda self, data, *, suffix: ("x", 0.1),
        raising=False,
    )
    files = {"audio": ("a.wav", _webm_bytes(), "audio/wav")}
    res = client.post("/api/voice/transcribe", files=files)
    assert res.status_code == 415


def test_transcribe_duration_cap(client, monkeypatch):
    """Whisper reports a duration above the cap -> 413."""
    monkeypatch.setattr(
        WhisperSTT,
        "transcribe",
        lambda self, data, *, suffix: ("ok", 999999.0),
        raising=False,
    )
    files = {"audio": ("a.wav", _wav_bytes(), "audio/wav")}
    res = client.post("/api/voice/transcribe", files=files)
    assert res.status_code == 413


# --------------------------------------------------------------------- rate limit


def test_rate_limit_enforced(client, monkeypatch):
    srv._voice_rate_limiter = RateLimiter(max_per_window=2, window_seconds=60)
    monkeypatch.setattr(
        KokoroTTS, "synthesize",
        lambda self, text, **kw: b"abc",
        raising=False,
    )
    assert client.post("/api/voice/speak", json={"text": "hi"}).status_code == 200
    assert client.post("/api/voice/speak", json={"text": "hi"}).status_code == 200
    res = client.post("/api/voice/speak", json={"text": "hi"})
    assert res.status_code == 429
    assert "Retry-After" in res.headers


def test_voice_disabled_returns_503(client, monkeypatch):
    """When VOICE_ENABLED=false the endpoints short-circuit to 503."""
    monkeypatch.setattr(srv.get_settings(), "voice_enabled", False, raising=False)
    res = client.post("/api/voice/speak", json={"text": "hi"})
    assert res.status_code == 503


# ----------------------------------------------------------------- /api/health


def test_health_includes_voice_block(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert "voice" in body
    voice = body["voice"]
    for key in (
        "enabled",
        "stt_model",
        "tts_voice",
        "stt_ready",
        "tts_ready",
        "max_audio_bytes",
        "tts_max_chars",
    ):
        assert key in voice
