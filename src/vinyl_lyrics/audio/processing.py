"""Audio buffering, silence detection, and spectrum processing."""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class AudioMetrics:
    """Current signal metrics."""

    rms: float
    peak: float
    dbfs: float
    bands: tuple[float, ...]


class CircularAudioBuffer:
    """Thread-safe circular mono float32 buffer."""

    def __init__(self, sample_rate: int, seconds: float = 15.0) -> None:
        self.sample_rate = sample_rate
        self.max_samples = max(1, int(sample_rate * seconds))
        self._samples: deque[float] = deque(maxlen=self.max_samples)
        self._lock = threading.Lock()

    def append(self, samples: np.ndarray) -> None:
        with self._lock:
            self._samples.extend(np.asarray(samples, dtype=np.float32).tolist())

    def latest(self, seconds: float) -> np.ndarray:
        count = max(1, int(seconds * self.sample_rate))
        with self._lock:
            values = list(self._samples)[-count:]
        return np.asarray(values, dtype=np.float32)


class SilenceDetector:
    """Hysteresis-based signal detector with minimum transition times."""

    def __init__(
        self,
        enter_dbfs: float = -48.0,
        exit_dbfs: float = -54.0,
        min_signal_seconds: float = 1.0,
        min_silence_seconds: float = 1.5,
    ) -> None:
        self.enter_dbfs = enter_dbfs
        self.exit_dbfs = exit_dbfs
        self.min_signal_seconds = min_signal_seconds
        self.min_silence_seconds = min_silence_seconds
        self.active = False
        self._above_for = 0.0
        self._below_for = 0.0

    def update(self, dbfs: float, elapsed_seconds: float) -> bool:
        """Update detector state and return whether signal is active."""
        if self.active:
            if dbfs <= self.exit_dbfs:
                self._below_for += elapsed_seconds
                if self._below_for >= self.min_silence_seconds:
                    self.active = False
                    self._below_for = 0.0
            else:
                self._below_for = 0.0
        elif dbfs >= self.enter_dbfs:
            self._above_for += elapsed_seconds
            if self._above_for >= self.min_signal_seconds:
                self.active = True
                self._above_for = 0.0
        else:
            self._above_for = 0.0
        return self.active


class SpectrumAnalyzer:
    """Calculate smoothed logarithmic spectrum bands."""

    def __init__(self, sample_rate: int = 44100, band_count: int = 64) -> None:
        self.sample_rate = sample_rate
        self.band_count = band_count
        self._smoothed = np.zeros(band_count, dtype=np.float32)
        self._edges = np.geomspace(40.0, 16000.0, band_count + 1)

    def analyze(self, samples: np.ndarray, gain_db: float = 0.0) -> AudioMetrics:
        if samples.size == 0:
            return AudioMetrics(0.0, 0.0, -120.0, tuple(self._smoothed.tolist()))
        gain = 10.0 ** (gain_db / 20.0)
        window = np.hanning(samples.size)
        amplified = np.asarray(samples, dtype=np.float32) * gain
        rms = float(np.sqrt(np.mean(np.square(amplified))))
        peak = float(np.max(np.abs(amplified)))
        dbfs = 20.0 * math.log10(max(rms, 1e-6))
        spectrum = np.abs(np.fft.rfft(amplified * window))
        frequencies = np.fft.rfftfreq(amplified.size, 1.0 / self.sample_rate)
        values = np.zeros(self.band_count, dtype=np.float32)
        for index in range(self.band_count):
            selected = (frequencies >= self._edges[index]) & (frequencies < self._edges[index + 1])
            if np.any(selected):
                values[index] = float(np.max(spectrum[selected]))
        maximum = float(np.max(values))
        if maximum > 0:
            values /= maximum
        attack = values > self._smoothed
        self._smoothed = np.where(
            attack,
            self._smoothed + (values - self._smoothed) * 0.65,
            self._smoothed + (values - self._smoothed) * 0.12,
        )
        return AudioMetrics(rms, peak, dbfs, tuple(self._smoothed.tolist()))
