"""Common audio source implementations."""

from __future__ import annotations

import sys
import threading
import warnings
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

warnings.filterwarnings(
    "ignore",
    message=r"Setting the shape on a NumPy array has been deprecated\.",
    category=DeprecationWarning,
    module=r"sounddevice",
)

AudioCallback = Callable[[np.ndarray], None]


@dataclass(frozen=True, slots=True)
class AudioSourceInfo:
    """Stable metadata for an available audio source."""

    source_id: str
    name: str
    source_type: str
    available: bool = True


class AudioSource(ABC):
    """Interface implemented by input and system-loopback sources."""

    info: AudioSourceInfo

    @abstractmethod
    def start(self, callback: AudioCallback) -> None:
        """Start delivering mono float32 frames to callback."""

    @abstractmethod
    def stop(self) -> None:
        """Stop delivering audio frames."""


class SoundDeviceSource(AudioSource):
    """Input-device source backed by sounddevice.InputStream."""

    def __init__(self, info: AudioSourceInfo, sample_rate: int, channels: int) -> None:
        import sounddevice

        self.info = info
        self.sample_rate = sample_rate
        self.channels = channels
        self._sounddevice = sounddevice
        self._stream: Any = None
        self._callback: AudioCallback | None = None
        self._lock = threading.Lock()

    def start(self, callback: AudioCallback) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            self._callback = None
            self._callback = callback
            self._stream = self._sounddevice.InputStream(
                device=int(self.info.source_id.removeprefix("input:")),
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                callback=self._on_audio,
            )
            self._stream.start()

    def _on_audio(self, data: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        del frames, time_info, status
        if self._callback is None:
            return
        samples = np.asarray(data, dtype=np.float32)
        mono = samples.mean(axis=1) if samples.ndim == 2 else samples.reshape(-1)
        self._callback(mono)

    def stop(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            self._callback = None


class WasapiLoopbackSource(AudioSource):
    """Optional Windows WASAPI loopback source using pyaudiowpatch."""

    def __init__(self, info: AudioSourceInfo, sample_rate: int) -> None:
        self.info = info
        self.sample_rate = sample_rate
        self._stream: Any = None
        self._channels = 1
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._callback: AudioCallback | None = None

    def start(self, callback: AudioCallback) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WASAPI loopback is only available on Windows.")
        try:
            import pyaudiowpatch
        except ImportError as error:
            raise RuntimeError("pyaudiowpatch is not installed.") from error

        self.stop()
        audio = pyaudiowpatch.PyAudio()
        device_index = int(self.info.source_id.removeprefix("loopback:"))
        device = audio.get_device_info_by_index(device_index)
        self._channels = max(1, int(device["maxInputChannels"]))
        self._callback = callback
        self._stop_event.clear()
        self._stream = audio.open(
            format=pyaudiowpatch.paInt16,
            channels=int(device["maxInputChannels"]),
            rate=int(device["defaultSampleRate"]),
            input=True,
            input_device_index=device_index,
            frames_per_buffer=1024,
        )
        self._thread = threading.Thread(target=self._read_loop, args=(audio,), daemon=True)
        self._thread.start()

    def _read_loop(self, audio: Any) -> None:
        del audio
        while not self._stop_event.is_set() and self._stream is not None:
            raw = self._stream.read(1024, exception_on_overflow=False)
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            mono = samples.reshape(-1, self._channels).mean(axis=1)
            if self._callback is not None:
                self._callback(mono)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        self._callback = None


def create_audio_source(
    info: AudioSourceInfo,
    sample_rate: int,
    channels: int,
) -> AudioSource:
    """Create a source from stable source metadata."""
    if info.source_type == "input":
        return SoundDeviceSource(info, sample_rate, channels)
    if info.source_type == "loopback":
        return WasapiLoopbackSource(info, sample_rate)
    raise ValueError(f"Unsupported audio source type: {info.source_type}")
