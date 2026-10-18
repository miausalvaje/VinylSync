"""Audio buffering, silence detection, and spectrum processing."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class AudioMetrics:
    """Current signal metrics."""

    rms: float
    peak: float
    dbfs: float
    bands: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SilenceState:
    """Immutable result of a silence detector update."""

    active: bool
    became_active: bool
    became_silent: bool
    silent_for: float


class CircularAudioBuffer:
    """Thread-safe circular mono float32 buffer."""

    def __init__(self, sample_rate: int, seconds: float = 15.0) -> None:
        self.sample_rate = sample_rate
        self.max_samples = max(1, int(sample_rate * seconds))
        self._samples = np.zeros(self.max_samples, dtype=np.float32)
        self._write_index = 0
        self._size = 0
        self._lock = threading.Lock()

    def append(self, samples: np.ndarray) -> None:
        values = np.asarray(samples, dtype=np.float32).reshape(-1)
        if values.size == 0:
            return
        if values.size > self.max_samples:
            values = values[-self.max_samples :]
        with self._lock:
            end = self._write_index + values.size
            if end <= self.max_samples:
                self._samples[self._write_index : end] = values
            else:
                first = self.max_samples - self._write_index
                self._samples[self._write_index :] = values[:first]
                self._samples[: end % self.max_samples] = values[first:]
            self._write_index = end % self.max_samples
            self._size = min(self.max_samples, self._size + values.size)

    def clear(self) -> None:
        """Discard all buffered audio (e.g. after a stream discontinuity)."""
        with self._lock:
            self._write_index = 0
            self._size = 0

    def latest(self, seconds: float) -> np.ndarray:
        count = max(1, int(seconds * self.sample_rate))
        count = min(count, self.max_samples)
        with self._lock:
            count = min(count, self._size)
            start = (self._write_index - count) % self.max_samples
            if count == 0:
                return np.empty(0, dtype=np.float32)
            if start < self._write_index or self._size < self.max_samples:
                return self._samples[start : start + count].copy()
            return np.concatenate(
                (self._samples[start:], self._samples[: self._write_index])
            ).astype(np.float32, copy=False)


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

    def update(self, dbfs: float, elapsed_seconds: float) -> SilenceState:
        """Update detector state and return signal transitions."""
        became_active = False
        became_silent = False
        if self.active:
            if dbfs <= self.exit_dbfs:
                self._below_for += elapsed_seconds
                if self._below_for >= self.min_silence_seconds:
                    self.active = False
                    became_silent = True
            else:
                self._below_for = 0.0
        elif dbfs >= self.enter_dbfs:
            self._above_for += elapsed_seconds
            if self._above_for >= self.min_signal_seconds:
                self.active = True
                self._above_for = 0.0
                self._below_for = 0.0
                became_active = True
        else:
            self._above_for = 0.0
            self._below_for += elapsed_seconds
        return SilenceState(self.active, became_active, became_silent, self._below_for)


class SpectrumAnalyzer:
    """Calculate smoothed logarithmic spectrum bands."""

    def __init__(self, sample_rate: int = 44100, band_count: int = 64) -> None:
        self.sample_rate = sample_rate
        self.band_count = band_count
        self._smoothed = np.zeros(band_count, dtype=np.float32)
        self._edges = np.geomspace(40.0, 16000.0, band_count + 1)
        self._fft_size = 2048
        self._window = np.hanning(self._fft_size)
        frequencies = np.fft.rfftfreq(self._fft_size, 1.0 / self.sample_rate)
        # Each band is a contiguous run of FFT bins (low bands may have none),
        # so all band maxima come from a single reduceat over the bin starts.
        band_of_bin = np.searchsorted(self._edges, frequencies, side="right") - 1
        starts = np.searchsorted(band_of_bin, np.arange(self.band_count), side="left")
        ends = np.searchsorted(band_of_bin, np.arange(self.band_count), side="right")
        self._filled_bands = np.flatnonzero(ends > starts)
        self._band_starts = starts[self._filled_bands]
        self._bins_end = int(ends[self._filled_bands[-1]]) if self._filled_bands.size else 0

    def analyze(self, samples: np.ndarray, gain_db: float = 0.0) -> AudioMetrics:
        """Analyze a short audio window for spectrum and level."""
        if samples.size == 0:
            return AudioMetrics(0.0, 0.0, -120.0, tuple(self._smoothed.tolist()))
        gain = 10.0 ** (gain_db / 20.0)
        amplified = np.asarray(samples, dtype=np.float32) * gain
        level_window = amplified[-max(1, int(self.sample_rate * 0.25)) :]
        rms = float(np.sqrt(np.mean(np.square(level_window))))
        peak = float(np.max(np.abs(level_window)))
        dbfs = 20.0 * math.log10(max(rms, 1e-6))
        fft_samples = amplified[-self._fft_size :]
        if fft_samples.size < self._fft_size:
            fft_samples = np.pad(fft_samples, (self._fft_size - fft_samples.size, 0))
        spectrum = np.abs(np.fft.rfft(fft_samples * self._window))
        values = np.zeros(self.band_count, dtype=np.float32)
        if self._filled_bands.size:
            band_power = np.maximum.reduceat(spectrum[: self._bins_end], self._band_starts)
            band_db = 20.0 * np.log10(np.maximum(band_power / self._fft_size, 1e-6))
            values[self._filled_bands] = np.clip((band_db + 80.0) / 80.0, 0.0, 1.0)
        attack = values > self._smoothed
        self._smoothed = np.where(
            attack,
            self._smoothed + (values - self._smoothed) * 0.65,
            self._smoothed + (values - self._smoothed) * 0.12,
        )
        return AudioMetrics(rms, peak, dbfs, tuple(self._smoothed.tolist()))

    def analyze_level(self, samples: np.ndarray, gain_db: float = 0.0) -> AudioMetrics:
        """Calculate RMS, peak, and dBFS without updating spectrum smoothing."""
        if samples.size == 0:
            return AudioMetrics(0.0, 0.0, -120.0, tuple(self._smoothed.tolist()))
        gain = 10.0 ** (gain_db / 20.0)
        amplified = np.asarray(samples, dtype=np.float32) * gain
        rms = float(np.sqrt(np.mean(np.square(amplified))))
        peak = float(np.max(np.abs(amplified)))
        dbfs = 20.0 * math.log10(max(rms, 1e-6))
        return AudioMetrics(rms, peak, dbfs, tuple(self._smoothed.tolist()))
