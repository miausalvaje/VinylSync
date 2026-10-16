import pytest

import vinyl_lyrics.audio.capture as capture_module
from vinyl_lyrics.audio.capture import ContinuousAudioCapture
from vinyl_lyrics.audio.source import AudioSourceInfo


class FakeSource:
    def __init__(self, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.started = False
        self.stop_calls = 0

    def start(self, callback) -> None:
        if self.fail_start:
            raise RuntimeError("start failed")
        self.started = True

    def stop(self) -> None:
        self.stop_calls += 1
        self.started = False


class FakeBuffer:
    def clear(self) -> None:
        pass


def test_failed_source_switch_restores_previous_source(monkeypatch) -> None:
    previous = FakeSource()
    replacement = FakeSource(fail_start=True)
    previous_info = AudioSourceInfo("old", "Old source", "input", True)
    replacement_info = AudioSourceInfo("new", "New source", "input", True)

    capture = ContinuousAudioCapture.__new__(ContinuousAudioCapture)
    capture.sample_rate = 44100
    capture.channels = 1
    capture.source = previous
    capture.source_info = previous_info
    capture._received_samples = 0
    capture._last_audio_monotonic = 0.0
    capture.buffer = FakeBuffer()

    monkeypatch.setattr(capture_module, "resolve_source", lambda source_id: replacement_info)
    monkeypatch.setattr(
        capture_module, "create_audio_source", lambda info, sample_rate, channels: replacement
    )

    with pytest.raises(RuntimeError, match="start failed"):
        capture.switch_source("new")

    assert capture.source is previous
    assert capture.source_info is previous_info
    assert previous.started is True
    assert replacement.stop_calls == 1
