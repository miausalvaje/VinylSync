"""Tests for the recognition debug WAV dump."""

from __future__ import annotations

import os
from pathlib import Path

from vinyl_lyrics.main import _maybe_dump_debug_wav


def test_debug_wav_disabled_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("VINYLSYNC_DEBUG_WAV_DIR", raising=False)
    assert _maybe_dump_debug_wav(b"RIFF", 12.0) is None
    assert list(tmp_path.iterdir()) == []


def test_debug_wav_dump_and_prune(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VINYLSYNC_DEBUG_WAV_DIR", str(tmp_path))
    for index in range(25):
        candidate = tmp_path / f"recog_20200101-000000_{index}.wav"
        candidate.write_bytes(b"RIFF")
        os.utime(candidate, (1000000 + index, 1000000 + index))

    path = _maybe_dump_debug_wav(b"RIFF", 12.0)

    assert path is not None and path.is_file()
    remaining = sorted(tmp_path.glob("recog_*.wav"))
    assert len(remaining) == 20
    assert path in remaining
