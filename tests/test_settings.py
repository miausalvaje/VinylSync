"""Tests for persistent user settings."""

from vinyl_lyrics.settings import SettingsStore, UserSettings


def test_settings_round_trip(tmp_path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    settings = UserSettings(source_id="input:4", gain_db=3.0, font_size=1.25)
    store.save(settings)
    loaded = store.load()
    assert loaded.source_id == "input:4"
    assert loaded.gain_db == 3.0
    assert loaded.font_size == 1.25
