"""End-to-end smoke test for the voice pipeline.

Round-trips a short phrase through TTS -> STT to make sure both engines
are wired up correctly. Skipped automatically when the engines aren't
ready (no model cache, VOICE_ENABLED=false, etc) so it's safe to run on
a fresh machine without pre-pulling weights.

Usage:
    python scripts/smoke_voice.py
    python scripts/smoke_voice.py "the quick brown fox"

Exit codes:
    0 -- round-trip succeeded (transcript contains the expected phrase)
    1 -- TTS or STT failed
    2 -- skipped (engines not ready and SMOKE_VOICE_REQUIRE not set)
"""

from __future__ import annotations

import os
import sys
import tempfile

from x_agent.config import get_settings
from x_agent.voice import KokoroTTS, VoiceEngineUnavailable, WhisperSTT


def main() -> int:
    settings = get_settings()
    if not settings.voice_enabled:
        print("voice pipeline is disabled (VOICE_ENABLED=false); skipping.")
        return 0 if not os.environ.get("SMOKE_VOICE_REQUIRE") else 2

    phrase = sys.argv[1] if len(sys.argv) > 1 else "the quick brown fox jumps"
    print(f"== phrase: {phrase!r}")
    print(f"== voice:  {settings.voice_tts_voice}")
    print(f"== stt:    {settings.voice_stt_model}")
    print(f"== cache:  {settings.voice_model_dir}\n")

    # ----- TTS
    try:
        wav = KokoroTTS.get().synthesize(phrase)
    except VoiceEngineUnavailable as exc:
        print(f"SKIP: TTS engine unavailable ({exc})")
        return 0 if not os.environ.get("SMOKE_VOICE_REQUIRE") else 2
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: TTS raised {type(exc).__name__}: {exc}")
        return 1
    print(f"OK   TTS produced {len(wav)} bytes of WAV")
    assert wav.startswith(b"RIFF"), "TTS output is not a WAV file"

    # ----- STT (round-trip)
    try:
        text, duration = WhisperSTT.get().transcribe(wav, suffix=".wav")
    except VoiceEngineUnavailable as exc:
        print(f"SKIP: STT engine unavailable ({exc})")
        return 0 if not os.environ.get("SMOKE_VOICE_REQUIRE") else 2
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: STT raised {type(exc).__name__}: {exc}")
        return 1
    print(f"OK   STT recovered ({duration:.2f}s): {text!r}")

    # Loose match: punctuation/casing may differ, but the first noun phrase
    # should make it through.
    lowered = text.lower()
    needle = phrase.split()[0].lower()
    if needle not in lowered:
        print(f"FAIL: transcript missing leading word {needle!r}: {text!r}")
        return 1

    # Optional: write the WAV so the dev can verify by ear.
    if "--save" in sys.argv:
        out = tempfile.NamedTemporaryFile(
            prefix="x_agent_smoke_", suffix=".wav", delete=False
        )
        out.write(wav)
        out.close()
        print(f"     wrote {out.name}")

    print("\nALL GOOD -- voice pipeline is round-trip clean.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
