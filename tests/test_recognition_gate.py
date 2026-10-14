"""Tests for recognition retry throttling."""

import time

import pytest

from vinyl_lyrics.audio.capture import RecognitionSnapshot
from vinyl_lyrics.main import ApplicationRuntime
from vinyl_lyrics.recognition.base import Track


class FakeCapture:
    def recognition_snapshot(
        self,
        seconds: float = 8.0,
        since: float | None = None,
        min_seconds: float = 0.0,
    ) -> RecognitionSnapshot | None:
        del since, min_seconds
        return RecognitionSnapshot(b"wav", time.monotonic() - seconds, seconds)


class FakeRecognizer:
    def __init__(self) -> None:
        self.calls = 0

    async def recognize(self, wav_bytes: bytes) -> Track | None:
        del wav_bytes
        self.calls += 1
        return None


@pytest.mark.asyncio
async def test_failed_recognition_is_throttled() -> None:
    runtime = object.__new__(ApplicationRuntime)
    runtime._recognition_in_progress = False
    runtime._next_recognition_at = 0.0
    runtime.failed_attempts = 0
    runtime.status = "listening"
    runtime.capture = FakeCapture()
    runtime.recognizer = FakeRecognizer()
    runtime.clients = set()
    runtime.recognition_window_seconds = 8.0
    runtime.public_state = lambda: {"state": runtime.status}
    runtime.broadcast = lambda payload=None: _completed()

    await runtime.recognize_once()
    assert runtime.recognizer.calls == 1
    assert runtime._next_recognition_at > time.monotonic()

    await runtime.recognize_once()
    assert runtime.recognizer.calls == 1


async def _completed() -> None:
    return None
