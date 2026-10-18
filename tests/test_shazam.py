import io
import wave

import pytest

from vinyl_lyrics.recognition.shazam import ShazamRecognizer


def _silent_wav(seconds: float, sample_rate: int = 44100) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * int(seconds * sample_rate))
    return buffer.getvalue()


def _fake_client(payload, seen=None):
    class _FakeClient:
        def __init__(self, **kwargs):
            if seen is not None:
                seen.update(kwargs)

        async def recognize(self, wav_bytes: bytes):
            del wav_bytes
            return payload

    return _FakeClient


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
    assert ShazamRecognizer._extract_offset(payload) == 42.5
    # timeskew is a speed ratio; +25 % is not a plausible turntable error.
    assert ShazamRecognizer._extract_rate(payload) is None


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
    assert ShazamRecognizer._extract_rate({"matches": [{"offset": 12.5}]}) is None


def test_timeskew_is_reported_as_playback_rate() -> None:
    # Measured with real Shazam: audio played 0.5 % slow reports -0.00493.
    payload = {"matches": [{"offset": 19.88, "timeskew": -0.0049321055}]}

    assert ShazamRecognizer._extract_rate(payload) == pytest.approx(0.99507, abs=1e-5)


async def test_recognize_without_offset_leaves_position_unknown(monkeypatch) -> None:
    """End to end: no offset in the payload means offset_seconds is None."""
    payload = {
        "matches": [{"id": "123"}],
        "track": {"title": "Telephone Line", "subtitle": "Electric Light Orchestra"},
    }

    monkeypatch.setattr("vinyl_lyrics.recognition.shazam.ShazamClient", _fake_client(payload))
    track = await ShazamRecognizer().recognize(b"wav")

    assert track is not None
    assert track.offset_seconds is None


@pytest.mark.parametrize(
    ("sample_seconds", "lead_in"),
    [(5.0, 0.0), (10.0, 0.0), (12.0, 1.0), (15.0, 2.5)],
)
async def test_offset_is_moved_to_the_sample_start(monkeypatch, sample_seconds, lead_in) -> None:
    """shazamio fingerprints the centred 10 s; Shazam reports that slice's start.

    Real Shazam, sample cut at 30.0 s of a known recording: 12 s -> 31.005,
    10 s -> 30.007, 15 s -> 32.502 (Kevin MacLeod, "Monkeys Spinning Monkeys").
    """
    payload = {
        "matches": [{"offset": 30.0 + lead_in, "timeskew": 0.0}],
        "track": {"title": "Song", "subtitle": "Artist"},
    }
    seen: dict = {}
    monkeypatch.setattr("vinyl_lyrics.recognition.shazam.ShazamClient", _fake_client(payload, seen))

    track = await ShazamRecognizer().recognize(_silent_wav(sample_seconds))

    assert track is not None
    assert track.offset_seconds == pytest.approx(30.0)
    assert track.playback_rate == pytest.approx(1.0)
    assert seen == {"segment_duration_seconds": 10}


async def test_lead_in_is_scaled_by_the_playback_rate(monkeypatch) -> None:
    payload = {
        "matches": [{"offset": 31.01, "timeskew": 0.01}],
        "track": {"title": "Song", "subtitle": "Artist"},
    }
    monkeypatch.setattr("vinyl_lyrics.recognition.shazam.ShazamClient", _fake_client(payload))

    track = await ShazamRecognizer().recognize(_silent_wav(12.0))

    assert track is not None
    assert track.offset_seconds == pytest.approx(30.0)
    assert track.playback_rate == pytest.approx(1.01)
