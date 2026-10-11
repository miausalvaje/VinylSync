"""LRCLIB access client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class LyricResult:
    """Normalized lyric payload returned by LRCLIB."""

    text: str
    synced: bool
    title: str
    artist: str
    album: str = ""
    duration: float | None = None
    cover_url: str | None = None


class LRCLIBClient:
    """Asynchronous LRCLIB client."""

    def __init__(
        self, base_url: str = "https://lrclib.net", user_agent: str = "vinyl-lyrics/0.1.0"
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/api"):
            self.base_url = self.base_url[:-4]
        self.user_agent = user_agent

    @staticmethod
    def _normalize(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_duration(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _match_score(
        candidate: dict[str, Any], title: str, artist: str, duration: float | None
    ) -> float:
        score = 0.0
        candidate_title = LRCLIBClient._normalize(
            candidate.get("trackName") or candidate.get("name") or candidate.get("title")
        )
        candidate_artist = LRCLIBClient._normalize(
            candidate.get("artistName") or candidate.get("artist") or candidate.get("artist_name")
        )
        candidate_duration = LRCLIBClient._normalize_duration(
            candidate.get("duration") or candidate.get("duration_ms")
        )

        if candidate_title and title:
            if candidate_title.lower() == title.lower():
                score += 4.0
            elif (
                title.lower() in candidate_title.lower() or candidate_title.lower() in title.lower()
            ):
                score += 2.0

        if candidate_artist and artist:
            if candidate_artist.lower() == artist.lower():
                score += 4.0
            elif (
                artist.lower() in candidate_artist.lower()
                or candidate_artist.lower() in artist.lower()
            ):
                score += 2.0

        if candidate_duration is not None and duration is not None:
            delta = abs(candidate_duration - duration)
            if delta <= 3.0:
                score += 3.0
            elif delta <= 10.0:
                score += 1.0

        return score

    async def _request_json(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        headers = {"User-Agent": self.user_agent}
        async with httpx.AsyncClient(
            timeout=15.0, headers=headers, follow_redirects=True
        ) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def get_lyrics(
        self,
        artist_name: str,
        track_name: str,
        album_name: str | None = None,
        duration: float | None = None,
    ) -> LyricResult | None:
        """Fetch lyrics from LRCLIB using a GET-first strategy and a search fallback."""
        params: dict[str, Any] = {
            "artist_name": artist_name,
            "track_name": track_name,
        }
        if album_name:
            params["album_name"] = album_name
        if duration is not None:
            params["duration"] = int(round(float(duration)))

        match = await self._request_json("/api/get", params)
        if match and isinstance(match, dict):
            result = self._normalize_lyrics_result(
                match, artist_name, track_name, album_name, duration
            )
            if result is not None:
                return result

        search_results = await self._request_json(
            "/api/search", {"track_name": track_name, "artist_name": artist_name}
        )
        candidates = search_results if isinstance(search_results, list) else []
        if not candidates:
            return None

        best_candidate = max(
            candidates,
            key=lambda item: self._match_score(item, track_name, artist_name, duration),
            default=None,
        )
        if best_candidate is None:
            return None

        return self._normalize_lyrics_result(
            best_candidate, artist_name, track_name, album_name, duration
        )

    async def search(self, query: str) -> list[dict[str, Any]]:
        """Search LRCLIB candidates for manual track correction."""
        payload = await self._request_json("/api/search", {"q": query})
        if not isinstance(payload, list):
            return []
        return [
            {
                "id": item.get("id"),
                "title": item.get("trackName") or item.get("title") or "",
                "artist": item.get("artistName") or item.get("artist") or "",
                "album": item.get("albumName") or item.get("album") or "",
                "duration": self._normalize_duration(item.get("duration")),
                "synced": bool(item.get("syncedLyrics")),
                "syncedLyrics": item.get("syncedLyrics"),
                "plainLyrics": item.get("plainLyrics"),
            }
            for item in payload
            if isinstance(item, dict)
        ]

    @staticmethod
    def _normalize_lyrics_result(
        candidate: dict[str, Any],
        artist_name: str,
        track_name: str,
        album_name: str | None,
        duration: float | None,
    ) -> LyricResult | None:
        synced_text = candidate.get("syncedLyrics")
        plain_text = candidate.get("plainLyrics")
        text = synced_text or plain_text
        if not text:
            return None

        return LyricResult(
            text=text,
            synced=bool(synced_text),
            title=LRCLIBClient._normalize(
                candidate.get("trackName") or candidate.get("name") or track_name
            ),
            artist=LRCLIBClient._normalize(
                candidate.get("artistName") or candidate.get("artist") or artist_name
            ),
            album=LRCLIBClient._normalize(
                candidate.get("albumName") or candidate.get("album") or (album_name or "")
            ),
            duration=LRCLIBClient._normalize_duration(
                candidate.get("duration") or candidate.get("duration_ms") or duration
            ),
            cover_url=candidate.get("coverArt")
            or candidate.get("cover_art")
            or candidate.get("albumArt")
            or None,
        )


__all__ = ["LRCLIBClient", "LyricResult"]
