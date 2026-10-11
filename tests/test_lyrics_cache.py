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


def test_cache_handles_curved_apostrophes_and_artist_variants(tmp_path) -> None:
    cache = LyricsCache(tmp_path / "lyrics.sqlite3")
    result = LyricResult(
        text="[00:01.00]We will meet again",
        synced=True,
        title="We’ll Meet Again",  # Unicode curved apostrophe
        artist="TheFatRat & Laura Brehm",
        duration=193.8,
    )

    cache.save(result)

    # ASCII apostrophe lookup should match
    found_ascii = cache.get("TheFatRat & Laura Brehm", "We'll Meet Again", 193.8)
    assert found_ascii is not None
    assert found_ascii.text == "[00:01.00]We will meet again"

    # Multi-artist primary fallback
    found_primary = cache.get("TheFatRat", "We'll Meet Again", None)
    assert found_primary is not None

    # Title with feat tag
    found_feat = cache.get("TheFatRat", "We'll Meet Again (feat. Laura Brehm)", 193.8)
    assert found_feat is not None


def test_cache_supports_aliases(tmp_path) -> None:
    cache = LyricsCache(tmp_path / "lyrics.sqlite3")
    result = LyricResult(
        text="[00:00.00]Hello",
        synced=True,
        title="Official Song",
        artist="Official Artist",
        duration=120.0,
    )

    cache.save(result, alias_artist="Recognized Artist", alias_title="Recognized Title")

    assert cache.get("Recognized Artist", "Recognized Title", 120.0) is not None
    assert cache.get("Official Artist", "Official Song", 120.0) is not None
