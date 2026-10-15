"""Continuous audio capture."""

from __future__ import annotations

import io
import logging
import time
import wave
from dataclasses import dataclass

import numpy as np

from vinyl_lyrics.audio.devices import resolve_source
from vinyl_lyrics.audio.processing import CircularAudioBuffer
from vinyl_lyrics.audio.source import AudioSource, AudioSourceInfo, create_audio_source

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecognitionSnapshot:
    """Audio window and its estimated position in the monotonic clock."""

    wav_bytes: bytes
    sample_start_monotonic: float
    sample_seconds: float


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
        try:
            self.source_info = resolve_source(source_id)
            self.source: AudioSource | None = create_audio_source(
                self.source_info, sample_rate, channels
            )
        except ValueError:
            self.source_info = AudioSourceInfo(
                source_id="unavailable",
                name="No audio source",
                source_type="input",
                available=False,
            )
            self.source = None
        self._received_samples = 0
        self._last_audio_monotonic = 0.0

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
        self._last_audio_monotonic = time.monotonic()

    def start(self) -> None:
        """Start the selected source."""
        if self.source is None:
            raise RuntimeError("No audio source is available. Select a source to start capture.")
        self.source.start(self._on_audio)

    def stop(self) -> None:
        """Stop the selected source."""
        if self.source is not None:
            self.source.stop()

    def switch_source(self, source_id: str) -> AudioSourceInfo:
        """Switch sources without restarting the process."""
        info = resolve_source(source_id)
        replacement = create_audio_source(info, self.sample_rate, self.channels)
        if self.source is not None:
            self.source.stop()
        self.source_info = info
        self.source = replacement
        self.source.start(self._on_audio)
        self._received_samples = 0
        self._last_audio_monotonic = 0.0
        self.buffer.clear()
        logger.info("Audio source changed to %s (%s)", info.name, info.source_id)
        return info

    def latest_samples(self, seconds: float) -> np.ndarray:
        """Return the latest buffered mono samples."""
        return self.buffer.latest(seconds)

    def clear_buffer(self) -> None:
        """Discard buffered audio after a stream discontinuity."""
        self.buffer.clear()
        self._last_audio_monotonic = 0.0

    def recognition_wav(self, seconds: float = 8.0) -> bytes:
        """Return the latest buffered recognition window as WAV."""
        return self.samples_to_wav(self.latest_samples(seconds), self.sample_rate)

    def recognition_snapshot(
        self,
        seconds: float = 8.0,
        since: float | None = None,
        min_seconds: float = 0.0,
    ) -> RecognitionSnapshot | None:
        """Return a recognition window together with its capture timestamp."""
        if since is None:
            samples = self.latest_samples(seconds)
        else:
            available_seconds = max(0.0, time.monotonic() - since)
            samples = self.latest_samples(min(seconds, available_seconds))
        sample_seconds = len(samples) / self.sample_rate
        if sample_seconds < min_seconds:
            return None
        if (
            self.source is not None
            and hasattr(self.source, "current_stream_monotonic")
            and callable(self.source.current_stream_monotonic)
            and getattr(self.source, "_anchor_monotonic", None) is not None
        ):
            end = self.source.current_stream_monotonic()
        else:
            end = self._last_audio_monotonic or time.monotonic()
        return RecognitionSnapshot(
            wav_bytes=self.samples_to_wav(samples, self.sample_rate),
            sample_start_monotonic=end - sample_seconds,
            sample_seconds=sample_seconds,
        )


AudioCapture = ContinuousAudioCapture

__all__ = ["AudioCapture", "ContinuousAudioCapture", "RecognitionSnapshot"]
