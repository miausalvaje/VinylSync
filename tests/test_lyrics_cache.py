from vinyl_lyrics.lyrics.cache import LyricsCache
from vinyl_lyrics.lyrics.lrclib import LyricResult


def test_cache_falls_back_when_track_duration_is_missing(tmp_path) -> None:
    cache = LyricsCache(tmp_path / "lyrics.sqlite3")
    result = LyricResult(
        text="[00:00.00]Ride",
        synced=True,
        title="Ride",
        artist="Twenty One Pilots",
        duration=215.0,
    )

    cache.save(result)

    assert cache.get("Twenty One Pilots", "Ride", None) is not None
    assert cache.get("Twenty One Pilots", "Ride", 215.0) is not None
