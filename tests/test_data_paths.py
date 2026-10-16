from pathlib import Path

from vinyl_lyrics.paths import resolve_data_path


def test_data_paths_use_configured_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VINYLSYNC_DATA_DIR", str(tmp_path))

    assert resolve_data_path("data/lyrics.sqlite3") == tmp_path / "lyrics.sqlite3"
    assert resolve_data_path("cache/extra.sqlite3") == tmp_path / "cache" / "extra.sqlite3"


def test_absolute_data_path_is_preserved(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VINYLSYNC_DATA_DIR", str(tmp_path))
    absolute = tmp_path.parent / "custom.sqlite3"

    assert resolve_data_path(absolute) == absolute
