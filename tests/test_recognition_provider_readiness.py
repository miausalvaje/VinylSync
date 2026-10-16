import pytest

from vinyl_lyrics.recognition import create_recognizer


@pytest.mark.parametrize("provider", ["acrcloud", "audd"])
def test_placeholder_providers_fail_fast(provider: str) -> None:
    with pytest.raises(ValueError, match="not implemented"):
        create_recognizer(provider=provider)
