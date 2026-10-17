from __future__ import annotations

import base64
from typing import Any

import pytest

from vinyl_lyrics.lyrics.kugou import KugouLyricsClient
from vinyl_lyrics.lyrics.lrclib import LyricResult
from vinyl_lyrics.lyrics.multi import MultiProviderLyricsClient
from vinyl_lyrics.lyrics.parser import has_implausible_timing


def _lrc(lines: list[tuple[float, str]]) -> str:
    return "\n".join(f"[{int(t // 60):02d}:{t % 60:05.2f}]{text}" for t, text in lines)


# Shape of LRCLIB's broken "Telephone Line": verses crammed half a second apart.
CRAMMED = _lrc(
    [
        (259.90, "(Do-wop, doobie do-do-wop)"),
        (260.82, "(Do-wah, do-lang)"),
        (262.10, "(Blue days, black nights)"),
        (263.42, "(Do-wah, do-lang)"),
        (264.20, "I look into the sky"),
        (265.16, "(The love you need ain't gonna see you through)"),
        (265.72, "And I wonder why"),
        (265.90, "(The little things you planned ain't coming true)"),
        (266.30, "Oh, oh, telephone line, give me some time"),
        (266.80, "I'm living in twilight"),
    ]
)

WELL_TIMED = _lrc(
    [
        (27.43, "Hello how are you"),
        (33.91, "Have you been all right"),
        (36.70, "Through all those lonely lonely lonely lonely lonely nights"),
        (42.11, "That's what I'd say"),
    ]
)

# Fast rap stays below the threshold (~55 characters per second).
FAST_RAP = _lrc([(i * 0.8, "Rapping really quick, every word on beat yo") for i in range(10)])


def test_crammed_lines_are_flagged_as_mistimed() -> None:
    assert has_implausible_timing(CRAMMED)


@pytest.mark.parametrize("lyrics", [WELL_TIMED, FAST_RAP, ""])
def test_plausible_lyrics_are_not_flagged(lyrics: str) -> None:
    assert not has_implausible_timing(lyrics)


def _result(text: str, synced: bool = True) -> LyricResult:
    return LyricResult(text=text, synced=synced, title="Telephone Line", artist="ELO")


class FakeProvider:
    def __init__(self, result: LyricResult | None) -> None:
        self.result = result
        self.calls = 0

    async def get_lyrics(self, *args: Any) -> LyricResult | None:
        self.calls += 1
        return self.result


@pytest.mark.asyncio
async def test_mistimed_lrclib_lyrics_fall_back_to_kugou() -> None:
    kugou = FakeProvider(_result(WELL_TIMED))
    client = MultiProviderLyricsClient(FakeProvider(_result(CRAMMED)), kugou)  # type: ignore[arg-type]

    result = await client.get_lyrics("Electric Light Orchestra", "Telephone Line", None, 281.0)

    assert result is not None
    assert result.text == WELL_TIMED
    assert kugou.calls == 1


@pytest.mark.asyncio
async def test_mistimed_lrclib_lyrics_are_kept_when_kugou_has_nothing() -> None:
    client = MultiProviderLyricsClient(
        FakeProvider(_result(CRAMMED)),  # type: ignore[arg-type]
        FakeProvider(None),  # type: ignore[arg-type]
    )

    result = await client.get_lyrics("Electric Light Orchestra", "Telephone Line", None, 281.0)

    assert result is not None
    assert result.text == CRAMMED


@pytest.mark.asyncio
async def test_well_timed_lrclib_lyrics_skip_kugou() -> None:
    kugou = FakeProvider(_result(WELL_TIMED))
    client = MultiProviderLyricsClient(FakeProvider(_result(WELL_TIMED)), kugou)  # type: ignore[arg-type]

    await client.get_lyrics("Electric Light Orchestra", "Telephone Line", None, 281.0)

    assert kugou.calls == 0


class _Response:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def json(self) -> dict[str, Any]:
        return self.payload


@pytest.mark.asyncio
async def test_kugou_falls_back_to_song_hash_when_artist_spelling_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, params: dict[str, Any]) -> _Response:
            requests.append((url, params))
            if url == KugouLyricsClient.SONG_SEARCH_URL:
                return _Response(
                    {
                        "data": {
                            "info": [
                                {"songname": "Mr. Blue Sky", "hash": "WRONG"},
                                {
                                    "songname": "Electric Light Orchestra Telephone line",
                                    "hash": "ABC123",
                                },
                            ]
                        }
                    }
                )
            if url == KugouLyricsClient.SEARCH_URL and params.get("hash") == "ABC123":
                return _Response(
                    {
                        "candidates": [
                            {
                                "id": "42",
                                "accesskey": "key",
                                "song": "Telephone line",
                                "singer": "The Electric Light Orchestra",
                                "duration": 279405,
                            }
                        ]
                    }
                )
            if url == KugouLyricsClient.DOWNLOAD_URL:
                return _Response({"content": base64.b64encode(WELL_TIMED.encode()).decode()})
            return _Response({"candidates": []})

    monkeypatch.setattr("vinyl_lyrics.lyrics.kugou.httpx.AsyncClient", FakeClient)

    result = await KugouLyricsClient().get_lyrics(
        "Electric Light Orchestra", "Telephone Line", None, 281.0
    )

    assert result is not None
    assert result.text == WELL_TIMED
    assert result.synced
    assert any(params.get("hash") == "ABC123" for _, params in requests)
    assert not any(params.get("hash") == "WRONG" for _, params in requests)
