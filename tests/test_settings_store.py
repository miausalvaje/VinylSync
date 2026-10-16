from vinyl_lyrics.settings import SettingsStore


def test_per_user_settings_paths_are_distinct_and_stable() -> None:
    alice = SettingsStore(user_key="alice")
    bob = SettingsStore(user_key="bob")
    alice_case_variant = SettingsStore(user_key="ALICE")

    assert alice.path != bob.path
    assert alice.path == alice_case_variant.path


def test_explicit_settings_path_takes_precedence(tmp_path) -> None:
    expected = tmp_path / "custom.json"
    assert SettingsStore(path=expected, user_key="alice").path == expected
