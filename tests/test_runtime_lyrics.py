import pytest

from vinyl_lyrics.lyrics.lrclib import LyricResult
from vinyl_lyrics.main import ApplicationRuntime
from vinyl_lyrics.recognition.base import Track


@pytest.mark.asyncio
async def test_load_lyrics_persists_result_for_public_state() -> None:
    runtime = object.__new__(ApplicationRuntime)
    runtime.cache = type(
        "Cache",
        (),
        {
            "get": lambda self, artist, title, duration: None,
            "save": lambda self, result: setattr(self, "saved", result),
        },
    )()
    runtime.player = type(
        "Player",
        (),
        {"current_track": type("CurrentTrack", (), {"duration": None})()},
    )()
    runtime.lyrics_client = type(
        "LyricsClient",
        (),
        {
            "get_lyrics": lambda self, *args: _lyrics(),
        },
    )()
    track = Track("Ride", "twenty one pilots", cover_url="https://example.test/cover.jpg")

    await runtime._load_lyrics(track)

    assert runtime.cache.saved.text == "[00:00.00]Ride"
    assert runtime.cache.saved.cover_url == track.cover_url


async def _lyrics() -> LyricResult:
    return LyricResult(
        text="[00:00.00]Ride",
        synced=True,
        title="Ride",
        artist="twenty one pilots",
    )
