from vinyl_lyrics.config import load_config


def test_default_config_uses_supported_silence_threshold_keys() -> None:
    audio = load_config()["audio"]
    assert audio["silence_enter_dbfs"] == -48
    assert audio["silence_exit_dbfs"] == -54
    assert "silence_threshold_db" not in audio
