"""LRCLIB tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from vinyl_lyrics.lyrics.lrclib import LRCLIBClient


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class DummyClient:
    def __init__(self, payload, *args, **kwargs):
        self.payload = payload
        self.get = AsyncMock(return_value=DummyResponse(payload))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_lrclib_returns_synced_lyrics_from_get():
    payload = {
        "trackName": "My Song",
        "artistName": "Artist",
        "syncedLyrics": "[00:00.00]Hello\n[00:02.00]World",
    }

    with patch("vinyl_lyrics.lyrics.lrclib.httpx.AsyncClient", return_value=DummyClient(payload)):
        client = LRCLIBClient(base_url="https://example.test/api")
        result = await client.get_lyrics("Artist", "My Song", duration=120.0)

    assert result is not None
    assert result.synced is True
    assert "Hello" in result.text
    assert "World" in result.text


@pytest.mark.asyncio
async def test_lrclib_falls_back_to_search_when_get_empty():
    get_payload = {}
    search_payload = [
        {
            "trackName": "My Song",
            "artistName": "Artist",
            "plainLyrics": "plain lyric",
            "duration": 121,
        }
    ]

    responses = [DummyClient(get_payload), DummyClient(search_payload)]

    with patch("vinyl_lyrics.lyrics.lrclib.httpx.AsyncClient", side_effect=responses):
        client = LRCLIBClient(base_url="https://example.test/api")
        result = await client.get_lyrics("Artist", "My Song", duration=120.0)

    assert result is not None
    assert result.synced is False
    assert result.text == "plain lyric"
