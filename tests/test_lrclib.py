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

    responses = [DummyClient(get_payload), DummyClient(get_payload)] + [
        DummyClient(search_payload),
        DummyClient(search_payload),
    ]

    with patch("vinyl_lyrics.lyrics.lrclib.httpx.AsyncClient", side_effect=responses):
        client = LRCLIBClient(base_url="https://example.test/api")
        result = await client.get_lyrics("Artist", "My Song", duration=120.0)

    assert result is not None
    assert result.synced is False
    assert result.text == "plain lyric"


@pytest.mark.asyncio
async def test_lrclib_accepts_alternative_key_names_and_plain_lyrics():
    payload = {
        "name": "My Song",
        "artist": "Artist",
        "plainLyrics": "plain lyric",
        "duration": 121,
    }

    with patch("vinyl_lyrics.lyrics.lrclib.httpx.AsyncClient", return_value=DummyClient(payload)):
        client = LRCLIBClient(base_url="https://example.test/api")
        result = await client.get_lyrics("Artist", "My Song", duration=120.0)

    assert result is not None
    assert result.synced is False
    assert result.text == "plain lyric"


@pytest.mark.asyncio
async def test_lrclib_falls_back_to_search_on_404():
    import httpx

    class Dummy404Response:
        status_code = 404

        def raise_for_status(self):
            request = httpx.Request("GET", "https://example.test/api/get")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("Not found", request=request, response=response)

        def json(self):
            return {}

    class Dummy404Client:
        def __init__(self, *args, **kwargs):
            self.get = AsyncMock(return_value=Dummy404Response())

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    search_payload = [
        {
            "trackName": "hide n seek",
            "artistName": "Ethan Bortnick",
            "syncedLyrics": "[00:10.00]Lyrics",
            "duration": 180,
        }
    ]

    responses = [Dummy404Client(), DummyClient(search_payload)]

    with patch("vinyl_lyrics.lyrics.lrclib.httpx.AsyncClient", side_effect=responses):
        client = LRCLIBClient(base_url="https://example.test/api")
        result = await client.get_lyrics("Ethan Bortnick", "hide n seek", duration=180.0)

    assert result is not None
    assert result.synced is True
    assert "Lyrics" in result.text


@pytest.mark.asyncio
async def test_lrclib_prefers_synced_over_plain_on_tie():
    """A plain exact hit must not win over an equally scoring synced entry."""
    plain_get = {
        "trackName": "Telephone Line",
        "artistName": "Electric Light Orchestra",
        "plainLyrics": "plain lyric",
        "duration": 282,
    }
    search_payload = [
        {
            "trackName": "Telephone Line",
            "artistName": "Electric Light Orchestra",
            "plainLyrics": "plain lyric",
            "duration": 282,
        },
        {
            "trackName": "Telephone Line",
            "artistName": "Electric Light Orchestra",
            "syncedLyrics": "[00:10.00]Synced",
            "plainLyrics": "plain lyric",
            "duration": 281,
        },
        {
            "trackName": "Telephone Line",
            "artistName": "Electric Light Orchestra",
            "syncedLyrics": "[00:10.00]Synced",
            "plainLyrics": "plain lyric",
            "duration": 284.333333,
        },
    ]
    responses = [
        DummyClient(plain_get),
        DummyClient(None),
        DummyClient(search_payload),
    ]

    with patch("vinyl_lyrics.lyrics.lrclib.httpx.AsyncClient", side_effect=responses):
        client = LRCLIBClient(base_url="https://example.test/api")
        result = await client.get_lyrics(
            "Electric Light Orchestra", "Telephone Line", duration=284.0
        )

    assert result is not None
    assert result.synced is True
    assert "Synced" in result.text


@pytest.mark.asyncio
async def test_lrclib_keeps_plain_get_hit_without_synced_same_song():
    """An unrelated synced entry must not replace a correct plain hit."""
    plain_get = {
        "trackName": "My Song",
        "artistName": "Artist",
        "plainLyrics": "plain lyric",
        "duration": 121,
    }
    unrelated_search = [
        {
            "trackName": "Other",
            "artistName": "Someone",
            "syncedLyrics": "[00:10.00]Unrelated",
            "duration": 120,
        }
    ]
    responses = [
        DummyClient(plain_get),
        DummyClient(None),
        DummyClient(unrelated_search),
        DummyClient(unrelated_search),
    ]

    with patch("vinyl_lyrics.lyrics.lrclib.httpx.AsyncClient", side_effect=responses):
        client = LRCLIBClient(base_url="https://example.test/api")
        result = await client.get_lyrics("Artist", "My Song", duration=120.0)

    assert result is not None
    assert result.synced is False
    assert result.text == "plain lyric"
