"""Continuous audio capture."""

from __future__ import annotations

import io
import logging
import wave

import numpy as np

from vinyl_lyrics.audio.devices import resolve_source
from vinyl_lyrics.audio.processing import CircularAudioBuffer
from vinyl_lyrics.audio.source import AudioSource, AudioSourceInfo, create_audio_source

logger = logging.getLogger(__name__)


class ContinuousAudioCapture:
    """Keep one audio source open and expose recent samples."""

    def __init__(
        self,
        source_id: str | None = None,
        sample_rate: int = 44100,
        channels: int = 1,
        buffer_seconds: float = 15.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.buffer = CircularAudioBuffer(sample_rate, buffer_seconds)
        self.source_info = resolve_source(source_id)
        self.source: AudioSource = create_audio_source(self.source_info, sample_rate, channels)
        self._received_samples = 0

    @staticmethod
    def samples_to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
        """Encode mono float32 samples as 16-bit PCM WAV bytes."""
        pcm = np.asarray(np.clip(samples, -1.0, 1.0) * 32767.0, dtype=np.int16)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm.tobytes())
        return buffer.getvalue()

    def _on_audio(self, samples: np.ndarray) -> None:
        self.buffer.append(samples)
        self._received_samples += int(samples.size)

    def start(self) -> None:
        """Start the selected source."""
        self.source.start(self._on_audio)

    def stop(self) -> None:
        """Stop the selected source."""
        self.source.stop()

    def switch_source(self, source_id: str) -> AudioSourceInfo:
        """Switch sources without restarting the process."""
        info = resolve_source(source_id)
        replacement = create_audio_source(info, self.sample_rate, self.channels)
        self.source.stop()
        self.source_info = info
        self.source = replacement
        self.source.start(self._on_audio)
        self._received_samples = 0
        logger.info("Audio source changed to %s (%s)", info.name, info.source_id)
        return info

    def latest_samples(self, seconds: float) -> np.ndarray:
        """Return the latest buffered mono samples."""
        return self.buffer.latest(seconds)

    def recognition_wav(self, seconds: float = 8.0) -> bytes:
        """Return the latest buffered recognition window as WAV."""
        return self.samples_to_wav(self.latest_samples(seconds), self.sample_rate)


AudioCapture = ContinuousAudioCapture

__all__ = ["AudioCapture", "ContinuousAudioCapture"]
