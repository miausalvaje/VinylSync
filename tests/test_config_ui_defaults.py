from vinyl_lyrics.main import _default_user_settings


def test_ui_defaults_are_loaded_from_config() -> None:
    settings = _default_user_settings(
        {
            "audio": {"gain_db": 3.0, "silence_enter_dbfs": -44.0, "silence_exit_dbfs": -50.0},
            "ui": {"font_size": 1.4, "manual_offset_seconds": -2.5},
        }
    )

    assert settings.gain_db == 3.0
    assert settings.silence_enter_dbfs == -44.0
    assert settings.silence_exit_dbfs == -50.0
    assert settings.font_size == 1.4
    assert settings.manual_offset_seconds == -2.5


def test_ui_defaults_fall_back_when_config_sections_are_missing() -> None:
    settings = _default_user_settings({})
    assert settings.font_size == 1.0
    assert settings.manual_offset_seconds == 0.0
