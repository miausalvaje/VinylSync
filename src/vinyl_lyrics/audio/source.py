"""Common audio source implementations."""

from __future__ import annotations

import logging
import sys
import threading
import time
import warnings
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

warnings.filterwarnings(
    "ignore",
    message=r"Setting the shape on a NumPy array has been deprecated\.",
    category=DeprecationWarning,
    module=r"sounddevice",
)

AudioCallback = Callable[[np.ndarray], None]

# Browser frames come over TCP: they arrive late or in bursts but are never
# lost in transit, so the sample count is the stream clock and arrival times
# only anchor it. A frame cannot arrive before it was recorded, so an "early"
# frame means the anchor is too late and moves it back at once, and a slow
# creep forward follows a sound card that runs a little slow. Frames that keep
# arriving late at the normal pace mean the browser really stopped sending
# (a suspended tab): only then does the anchor jump forward. A late frame
# followed by its backlog is just a stall and leaves the clock alone.
_GAP_SECONDS = 0.25
_GAP_CONFIRM_SECONDS = 0.3
_ANCHOR_CREEP = 0.002


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


class BrowserStreamSource(AudioSource):
    """Audio source receiving PCM Int16 mono frames from a browser WebSocket client."""

    def __init__(self, info: AudioSourceInfo, sample_rate: int) -> None:
        self.info = info
        self.sample_rate = sample_rate
        self._callback: AudioCallback | None = None
        self._lock = threading.Lock()
        self._active = False
        self._active_client_id: str | None = None
        self._client_sample_rate = sample_rate
        self._anchor_monotonic: float | None = None
        self._total_samples_delivered = 0
        self._last_received_monotonic: float | None = None
        self._last_log_monotonic: float | None = None
        self._late_since: float | None = None
        self._late_min = 0.0

    @property
    def active_client_id(self) -> str | None:
        with self._lock:
            return self._active_client_id

    def start(self, callback: AudioCallback) -> None:
        with self._lock:
            self._callback = callback
            self._active = True

    def stop(self) -> None:
        with self._lock:
            self._active = False
            self._callback = None
            self._active_client_id = None
            self._anchor_monotonic = None
            self._total_samples_delivered = 0
            self._last_received_monotonic = None
            self._last_log_monotonic = None
            self._late_since = None

    def reset_stream(self, client_id: str | None, client_sample_rate: int) -> None:
        """Reset the stream clock and configuration for a newly connected sender."""
        with self._lock:
            self._active_client_id = client_id
            self._client_sample_rate = client_sample_rate
            self._anchor_monotonic = None
            self._total_samples_delivered = 0
            self._last_received_monotonic = None
            self._last_log_monotonic = None
            self._late_since = None

    def current_stream_monotonic(self, now: float | None = None) -> float:
        """Return the stream timestamp corresponding to the end of buffered samples."""
        with self._lock:
            current_now = time.monotonic() if now is None else now
            if self._anchor_monotonic is None:
                return current_now
            return self._anchor_monotonic + (self._total_samples_delivered / self.sample_rate)

    def feed_pcm16_frame(self, data: bytes, now: float | None = None) -> None:
        """Process, resample, timestamp, and deliver a binary PCM Int16 mono frame."""
        with self._lock:
            if not self._active or self._callback is None or not data:
                return
            pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            if pcm.size == 0:
                return

            t_now = time.monotonic() if now is None else now
            if self._last_log_monotonic is None or t_now - self._last_log_monotonic >= 5.0:
                self._last_log_monotonic = t_now
                peak = float(np.max(np.abs(pcm)))
                logger.info(
                    "Browser audio frame: bytes=%d, peak=%.4f, client_sr=%d, sr=%d, delivered=%d",
                    len(data),
                    peak,
                    self._client_sample_rate,
                    self.sample_rate,
                    self._total_samples_delivered,
                )

            if self._client_sample_rate != self.sample_rate:
                in_len = len(pcm)
                out_len = int(round(in_len * (self.sample_rate / self._client_sample_rate)))
                if out_len <= 0:
                    return
                x_in = np.linspace(0.0, 1.0, in_len, endpoint=False)
                x_out = np.linspace(0.0, 1.0, out_len, endpoint=False)
                samples = np.interp(x_out, x_in, pcm).astype(np.float32)
            else:
                samples = pcm

            if self._anchor_monotonic is None:
                # The first frame was recorded before it arrived.
                self._anchor_monotonic = t_now - len(samples) / self.sample_rate
                self._total_samples_delivered = 0
            else:
                self._update_anchor(t_now, len(samples))

            self._callback(samples)
            self._total_samples_delivered += len(samples)
            self._last_received_monotonic = t_now

    def _update_anchor(self, t_now: float, frame_samples: int) -> None:
        """Follow the earliest arrivals; see the module notes on browser frames."""
        assert self._anchor_monotonic is not None
        frame_end = self._anchor_monotonic + (
            (self._total_samples_delivered + frame_samples) / self.sample_rate
        )
        lateness = t_now - frame_end
        if lateness <= _GAP_SECONDS:
            self._late_since = None
            if lateness < 0.0:
                self._anchor_monotonic += lateness
            else:
                self._anchor_monotonic += _ANCHOR_CREEP * lateness
            return
        if self._late_since is None:
            self._late_since = t_now
            self._late_min = lateness
            return
        self._late_min = min(self._late_min, lateness)
        if t_now - self._late_since >= _GAP_CONFIRM_SECONDS:
            logger.info("Browser audio resumed after a %.2fs gap", self._late_min)
            self._anchor_monotonic += self._late_min
            self._late_since = None


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
    if info.source_type == "browser":
        return BrowserStreamSource(info, sample_rate)
    raise ValueError(f"Unsupported audio source type: {info.source_type}")
