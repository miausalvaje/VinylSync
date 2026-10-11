from vinyl_lyrics.recognition.shazam import ShazamRecognizer


def test_extracts_shazam_track_from_top_level_response() -> None:
    payload = {
        "matches": [{"id": "123"}],
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
