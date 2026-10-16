from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import pytest

from vinyl_lyrics.recognition import ACRCloudRecognizer, create_recognizer
from vinyl_lyrics.recognition.acrcloud import _IDENTIFY_PATH


def _config(**overrides: str) -> dict[str, str]:
    return {
        "host": overrides.get("host", "identify-eu-west-1.acrcloud.com"),
        "access_key": overrides.get("access_key", "test-access-key"),
        "access_secret": overrides.get("access_secret", "test-access-secret"),
    }


def test_signature_matches_acrcloud_v1_spec() -> None:
    timestamp = "1700000000"
    expected_string = "\n".join(("POST", _IDENTIFY_PATH, "key", "audio", "1", timestamp))
    expected = base64.b64encode(
        hmac.new(b"secret", expected_string.encode(), hashlib.sha1).digest()
    ).decode()

    assert ACRCloudRecognizer._build_signature("key", "secret", timestamp) == expected


@pytest.mark.parametrize(
    ("field", "env_name"),
    [
        ("host", "ACR_HOST"),
        ("access_key", "ACR_ACCESS_KEY"),
        ("access_secret", "ACR_ACCESS_SECRET"),
    ],
)
def test_missing_credentials_fail_with_actionable_error(field: str, env_name: str) -> None:
    config = _config()
    config[field] = ""

    with pytest.raises(ValueError, match=env_name):
        ACRCloudRecognizer(config)


def test_acrcloud_host_must_use_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        ACRCloudRecognizer(_config(host="http://identify.example.com"))


def test_factory_creates_acrcloud_provider_from_config() -> None:
    recognizer = create_recognizer(
        config={
            "recognition": {
                "provider": "acrcloud",
                "timeout_seconds": 12,
                "acrcloud": _config(),
            }
        }
    )

    assert isinstance(recognizer, ACRCloudRecognizer)
    assert recognizer.timeout == 12
    assert recognizer.base_url == "https://identify-eu-west-1.acrcloud.com"


def test_build_track_normalizes_acrcloud_metadata() -> None:
    track = ACRCloudRecognizer._build_track(
        {
            "title": "Song title",
            "artists": [{"name": "Artist One"}, {"name": "Artist Two"}],
            "album": {"name": "Album title"},
            "duration_ms": 215000,
            "play_offset_ms": 43000,
            "external_metadata": {
                "spotify": {
                    "album": {
                        "images": [{"url": "https://example.com/cover.jpg"}],
                    },
                },
            },
        }
    )

    assert track is not None
    assert track.title == "Song title"
    assert track.artist == "Artist One, Artist Two"
    assert track.album == "Album title"
    assert track.duration == 215
    assert track.offset_seconds == 43
    assert track.cover_url == "https://example.com/cover.jpg"


@pytest.mark.asyncio
async def test_recognize_posts_signed_multipart_and_returns_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "status": {"code": 0, "msg": "Success"},
                "metadata": {
                    "music": [
                        {
                            "title": "Song title",
                            "artists": [{"name": "Artist"}],
                            "album": {"name": "Album"},
                            "duration_ms": 200000,
                            "play_offset_ms": 25000,
                        }
                    ]
                },
            }

    class FakeClient:
        def __init__(self, timeout: float) -> None:
            calls["timeout"] = timeout

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            calls["url"] = url
            calls.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr("vinyl_lyrics.recognition.acrcloud.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(time, "time", lambda: 1700000000)

    recognizer = ACRCloudRecognizer(_config(), timeout=7)
    track = await recognizer.recognize(b"RIFFfake-wav-data")

    assert track is not None
    assert track.title == "Song title"
    assert track.artist == "Artist"
    assert track.duration == 200
    assert track.offset_seconds == 25
    assert calls["url"] == f"https://identify-eu-west-1.acrcloud.com{_IDENTIFY_PATH}"
    assert calls["timeout"] == 7
    assert calls["data"]["access_key"] == "test-access-key"
    assert calls["data"]["sample_bytes"] == str(len(b"RIFFfake-wav-data"))
    assert calls["data"]["signature"] == ACRCloudRecognizer._build_signature(
        "test-access-key", "test-access-secret", "1700000000"
    )
    assert calls["files"]["sample"][0] == "vinylsync.wav"


@pytest.mark.asyncio
async def test_no_match_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"status": {"code": 1001, "msg": "No result"}}

    class FakeClient:
        def __init__(self, timeout: float) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("vinyl_lyrics.recognition.acrcloud.httpx.AsyncClient", FakeClient)
    assert await ACRCloudRecognizer(_config()).recognize(b"sample") is None


@pytest.mark.asyncio
async def test_empty_sample_skips_network_request() -> None:
    assert await ACRCloudRecognizer(_config()).recognize(b"") is None


@pytest.mark.asyncio
async def test_oversized_sample_is_rejected() -> None:
    recognizer = ACRCloudRecognizer(_config())
    with pytest.raises(ValueError, match="5 MiB"):
        await recognizer.recognize(b"x" * (5 * 1024 * 1024 + 1))


@pytest.mark.parametrize("provider", ["audd"])
def test_unimplemented_providers_still_fail_fast(provider: str) -> None:
    with pytest.raises(ValueError, match="not implemented"):
        create_recognizer(provider=provider)
