"""Record one recognition window and print the raw provider response."""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from vinyl_lyrics.audio.capture import ContinuousAudioCapture
from vinyl_lyrics.config import load_config
from vinyl_lyrics.recognition import create_recognizer


async def main() -> None:
    """Capture eight seconds and display raw and normalized recognition data."""
    config = load_config()
    audio = config.get("audio", {})
    capture = ContinuousAudioCapture(
        source_id=audio.get("source_id"),
        sample_rate=int(audio.get("sample_rate", 44100)),
        channels=1,
        buffer_seconds=float(audio.get("buffer_seconds", 15.0)),
    )
    recognizer = create_recognizer(config)
    capture.start()
    try:
        print("Capturing audio. Play a song and wait eight seconds...")
        await asyncio.sleep(8.5)
        snapshot = capture.recognition_snapshot(8.0)
        print(f"Captured {snapshot.sample_seconds:.2f} seconds.")
        if hasattr(recognizer, "recognize_raw"):
            raw = await recognizer.recognize_raw(snapshot.wav_bytes)
            print(json.dumps(raw, indent=2, ensure_ascii=False, default=str))
            print("\nNormalized track:")
            payload = recognizer._extract_track(raw)
            print(recognizer._build_track(payload) if payload is not None else None)
        else:
            print(await recognizer.recognize(snapshot.wav_bytes))
    finally:
        capture.stop()


if __name__ == "__main__":
    asyncio.run(main())
