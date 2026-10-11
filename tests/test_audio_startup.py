from unittest.mock import patch

import pytest

from vinyl_lyrics.audio.capture import ContinuousAudioCapture
from vinyl_lyrics.audio.devices import list_audio_sources


def test_audio_source_listing_is_empty_when_portaudio_is_unavailable() -> None:
    with patch.dict("sys.modules", {"sounddevice": None}):
        assert list_audio_sources() == []


def test_capture_can_be_constructed_without_audio_devices() -> None:
    with patch("vinyl_lyrics.audio.capture.resolve_source", side_effect=ValueError("none")):
        capture = ContinuousAudioCapture()

    assert capture.source is None
    assert capture.source_info.available is False
    with pytest.raises(RuntimeError, match="No audio source"):
        capture.start()
