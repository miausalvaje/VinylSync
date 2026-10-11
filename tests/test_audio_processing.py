"""Tests for continuous audio analysis."""

import numpy as np

from vinyl_lyrics.audio.processing import SilenceDetector, SpectrumAnalyzer


def test_silence_detector_uses_hysteresis_and_minimum_times() -> None:
    detector = SilenceDetector(
        enter_dbfs=-40.0,
        exit_dbfs=-50.0,
        min_signal_seconds=0.5,
        min_silence_seconds=1.0,
    )

    assert detector.update(-35.0, 0.2).active is False
    result = detector.update(-35.0, 0.3)
    assert result.active is True
    assert result.became_active is True
    assert detector.update(-45.0, 0.5).active is True
    assert detector.update(-55.0, 0.5).active is True
    result = detector.update(-55.0, 0.5)
    assert result.active is False
    assert result.became_silent is True
    assert result.silent_for == 1.0


def test_one_kilohertz_tone_activates_expected_spectrum_band() -> None:
    sample_rate = 44100
    samples = np.sin(2 * np.pi * 1000 * np.arange(sample_rate) / sample_rate).astype(np.float32)
    metrics = SpectrumAnalyzer(sample_rate).analyze(samples)
    strongest = int(np.argmax(metrics.bands))
    assert 25 <= strongest <= 45
    assert metrics.rms > 0.5


def test_quiet_signal_does_not_fill_spectrum_bands() -> None:
    sample_rate = 44100
    samples = (0.001 * np.sin(2 * np.pi * 1000 * np.arange(2048) / sample_rate)).astype(np.float32)

    metrics = SpectrumAnalyzer(sample_rate).analyze(samples)

    assert max(metrics.bands) < 0.5
