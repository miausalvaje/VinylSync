import pytest

from vinyl_lyrics.lyrics.lrclib import LyricResult
from vinyl_lyrics.main import ApplicationRuntime
from vinyl_lyrics.recognition.base import Track
from vinyl_lyrics.state.player import PlayerState


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


@pytest.mark.asyncio
async def test_plain_cache_upgrades_to_synced() -> None:
    """A cached plain result must not block a synced refetch forever."""
    saved: dict[str, LyricResult] = {}

    class UpgradeCache:
        def get(self, artist: str, title: str, duration: float | None) -> LyricResult | None:
            return LyricResult(
                text="plain", synced=False, title=title, artist=artist, duration=duration
            )

        def save(
            self,
            result: LyricResult,
            alias_artist: str | None = None,
            alias_title: str | None = None,
        ) -> None:
            saved["result"] = result

    class SyncedLyricsClient:
        async def get_lyrics(self, *args: object) -> LyricResult:
            return LyricResult(
                text="[00:01.00]Synced", synced=True, title="Ride", artist="twenty one pilots"
            )

    runtime = object.__new__(ApplicationRuntime)
    runtime.cache = UpgradeCache()
    runtime.lyrics_client = SyncedLyricsClient()
    runtime.player = type("Player", (), {"current_track": None})()

    await runtime._load_lyrics(Track("Ride", "twenty one pilots"))

    assert saved["result"].synced is True


@pytest.mark.asyncio
async def test_load_lyrics_gives_the_track_its_sung_line_starts() -> None:
    """Instrumental marker lines (empty text) are not sung lines."""

    class SyncedCache:
        def get(self, artist: str, title: str, duration: float | None) -> LyricResult | None:
            return LyricResult(
                text="[04:06.74]Waitin'\n[04:21.33] \n[05:39.77]Back",
                synced=True,
                title=title,
                artist=artist,
            )

    runtime = object.__new__(ApplicationRuntime)
    runtime.cache = SyncedCache()
    runtime.player = PlayerState()
    runtime.player.set_track("Paladin Strait", "twenty one pilots")

    await runtime._load_lyrics(Track("Paladin Strait", "twenty one pilots"))

    assert runtime.player.current_track is not None
    assert runtime.player.current_track.lyric_line_starts == (246.74, 339.77)
