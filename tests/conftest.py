"""Shared test fixtures."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_user_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests away from the real settings and data directories.

    Without this, a test that switches the audio source persists it to the
    user's settings.json and the next run starts from that source.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setattr("vinyl_lyrics.settings.user_config_dir", lambda _app: str(config_dir))
    monkeypatch.setenv("VINYLSYNC_DATA_DIR", str(tmp_path / "data"))
