from __future__ import annotations

from typing import Any

import pytest

from vinyl_lyrics.recognition import FallbackRecognizer, Track


class FakeRecognizer:
    def __init__(self, name: str, result: Track | None = None, error: Exception | None = None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = 0

    async def recognize(self, wav_bytes: bytes) -> Track | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_fallback_is_called_when_primary_returns_no_match() -> None:
    primary = FakeRecognizer("shazam")
    expected = Track(title="Song", artist="Artist")
    fallback = FakeRecognizer("acrcloud", result=expected)
    recognizer = FallbackRecognizer([primary, fallback])

    result = await recognizer.recognize(b"sample")

    assert result is expected
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_fallback_is_called_when_primary_raises() -> None:
    primary = FakeRecognizer("shazam", error=RuntimeError("Shazam unavailable"))
    expected = Track(title="Song", artist="Artist")
    fallback = FakeRecognizer("acrcloud", result=expected)
    recognizer = FallbackRecognizer([primary, fallback])

    result = await recognizer.recognize(b"sample")

    assert result is expected
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_all_no_matches_returns_none() -> None:
    primary = FakeRecognizer("shazam")
    fallback = FakeRecognizer("acrcloud")
    recognizer = FallbackRecognizer([primary, fallback])

    assert await recognizer.recognize(b"sample") is None


@pytest.mark.asyncio
async def test_all_provider_errors_raise_last_error() -> None:
    primary = FakeRecognizer("shazam", error=RuntimeError("first failure"))
    fallback = FakeRecognizer("acrcloud", error=TimeoutError("second failure"))
    recognizer = FallbackRecognizer([primary, fallback])

    with pytest.raises(TimeoutError, match="second failure"):
        await recognizer.recognize(b"sample")


def test_fallback_chain_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="At least one"):
        FallbackRecognizer([])  # type: ignore[arg-type]
