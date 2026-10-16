import pytest

from vinyl_lyrics.recognition import ACRCloudRecognizer, create_recognizer


def test_acrcloud_factory_requires_credentials() -> None:
    with pytest.raises(ValueError, match="ACR_HOST"):
        create_recognizer(
            provider="acrcloud",
            config={"recognition": {"acrcloud": {}}},
        )


def test_audd_placeholder_fails_fast() -> None:
    with pytest.raises(ValueError, match="not implemented"):
        create_recognizer(provider="audd")


def test_shazam_remains_the_default_provider() -> None:
    from vinyl_lyrics.recognition.shazam import ShazamRecognizer

    assert isinstance(create_recognizer(), ShazamRecognizer)


def test_acrcloud_provider_can_be_created_with_credentials() -> None:
    recognizer = create_recognizer(
        provider="acrcloud",
        config={
            "recognition": {
                "acrcloud": {
                    "host": "identify-eu-west-1.acrcloud.com",
                    "access_key": "key",
                    "access_secret": "secret",
                }
            }
        },
    )

    assert isinstance(recognizer, ACRCloudRecognizer)
