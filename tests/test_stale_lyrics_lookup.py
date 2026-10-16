import asyncio

from vinyl_lyrics.main import ApplicationRuntime
from vinyl_lyrics.lyrics.lrclib import LyricResult
from vinyl_lyrics.recognition.base import Track
from vinyl_lyrics.state.player import PlayerState


class FakeCache:
    def __init__(self) -> None:
        self.saved = []

    def get(self, artist, title, duration):
        return None

    def save(self, result, alias_artist=None, alias_title=None):
        self.saved.append(result)


class FakeLyricsClient:
    def __init__(self, result) -> None:
        self.result = result

    async def get_lyrics(self, artist_name, track_name, album_name=None, duration=None):
        await asyncio.sleep(0)
        return self.result


def test_stale_lyrics_result_does_not_change_current_track_duration() -> None:
    runtime = ApplicationRuntime.__new__(ApplicationRuntime)
    runtime.player = PlayerState()
    current = runtime.player.set_track("Track B", "Artist B", duration=210.0)
    runtime.cache = FakeCache()
    result = LyricResult(
        text="[00:00.00]Track A",
        synced=True,
        title="Track A",
        artist="Artist A",
        duration=123.0,
    )
    runtime.lyrics_client = FakeLyricsClient(result)

    asyncio.run(runtime._load_lyrics(Track("Track A", "Artist A", duration=123.0)))

    assert current.duration == 210.0
    assert runtime.cache.saved == [result]
