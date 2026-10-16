import asyncio

from vinyl_lyrics.main import ApplicationRuntime
from vinyl_lyrics.server.models import TrackOverride
from vinyl_lyrics.settings import UserSettings


def test_manual_override_does_not_use_lyric_offset_as_track_position() -> None:
    runtime = ApplicationRuntime.__new__(ApplicationRuntime)
    runtime.settings = UserSettings(manual_offset_seconds=12.0)
    captured = []
    runtime._apply_track = lambda track, locked: captured.append((track, locked))

    async def no_broadcast(payload=None):
        return None

    runtime.broadcast = no_broadcast

    asyncio.run(runtime.override_track(TrackOverride(title="Song", artist="Artist")))

    track, locked = captured[0]
    assert track.offset_seconds is None
    assert locked is True
