from vinyl_lyrics.recognition.shazam import ShazamRecognizer


def test_extracts_shazam_track_from_top_level_response() -> None:
    payload = {
        "matches": [{"id": "123", "offset": 42.5, "timeskew": 0.25}],
        "track": {
            "title": "Example Song",
            "subtitle": "Example Artist",
            "sections": [{"metadata": [{"title": "Album", "text": "Example Album"}]}],
            "images": {"coverart": "https://example.test/cover.jpg"},
        },
    }

    extracted = ShazamRecognizer._extract_track(payload)

    assert extracted is not None
    track = ShazamRecognizer._build_track(extracted)
    assert track is not None
    assert track.title == "Example Song"
    assert track.artist == "Example Artist"
    assert track.cover_url == "https://example.test/cover.jpg"
    assert ShazamRecognizer._extract_offset(payload) == 42.75


def test_builds_track_with_zero_offset_without_falling_back() -> None:
    track = ShazamRecognizer._build_track(
        {"title": "Song", "artist": "Artist", "offset_seconds": 0.0}
    )

    assert track is not None
    assert track.offset_seconds == 0.0


def test_missing_offset_reports_unknown_instead_of_zero() -> None:
    """A match without offset must not fabricate position 0.0."""
    assert ShazamRecognizer._extract_offset({"matches": [{}]}) is None
    assert ShazamRecognizer._extract_offset({"matches": [{"timeskew": 0.25}]}) is None
    assert ShazamRecognizer._extract_offset({"matches": []}) is None
    assert ShazamRecognizer._extract_offset({}) is None


def test_offset_without_timeskew_is_preserved() -> None:
    assert ShazamRecognizer._extract_offset({"matches": [{"offset": 12.5}]}) == 12.5
    assert ShazamRecognizer._extract_offset({"matches": [{"offset": 0.0}]}) == 0.0


async def test_recognize_without_offset_leaves_position_unknown(monkeypatch) -> None:
    """End to end: no offset in the payload means offset_seconds is None."""
    payload = {
        "matches": [{"id": "123"}],
        "track": {"title": "Telephone Line", "subtitle": "Electric Light Orchestra"},
    }

    class _FakeClient:
        async def recognize(self, wav_bytes: bytes):
            del wav_bytes
            return payload

    monkeypatch.setattr("vinyl_lyrics.recognition.shazam.ShazamClient", lambda: _FakeClient())
    track = await ShazamRecognizer().recognize(b"wav")

    assert track is not None
    assert track.offset_seconds is None
