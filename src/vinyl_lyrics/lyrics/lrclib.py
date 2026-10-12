"""LRCLIB access client."""

from __future__ import annotations

import re
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
        self, base_url: str = "https://lrclib.net", user_agent: str = "VinylSync/0.1.0"
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
    def _candidate_text(candidate: dict[str, Any]) -> str:
        text = (
            candidate.get("syncedLyrics")
            or candidate.get("plainLyrics")
            or candidate.get("lyrics")
            or candidate.get("text")
            or candidate.get("lyric")
        )
        return LRCLIBClient._normalize(text) if text is not None else ""

    @staticmethod
    def _match_score(
        candidate: dict[str, Any], title: str, artist: str, duration: float | None
    ) -> float:
        score = 0.0
        candidate_title = LRCLIBClient._normalize(
            candidate.get("trackName")
            or candidate.get("track_name")
            or candidate.get("name")
            or candidate.get("title")
        )
        candidate_artist = LRCLIBClient._normalize(
            candidate.get("artistName") or candidate.get("artist_name") or candidate.get("artist")
        )
        candidate_duration = LRCLIBClient._normalize_duration(
            candidate.get("duration") or candidate.get("duration_ms")
        )

        if candidate_title and title:
            if candidate_title.casefold() == title.casefold():
                score += 4.0
            elif (
                title.casefold() in candidate_title.casefold()
                or candidate_title.casefold() in title.casefold()
            ):
                score += 2.0

        if candidate_artist and artist:
            if candidate_artist.casefold() == artist.casefold():
                score += 4.0
            elif (
                artist.casefold() in candidate_artist.casefold()
                or candidate_artist.casefold() in artist.casefold()
            ):
                score += 2.0

        if candidate_duration is not None and duration is not None:
            delta = abs(candidate_duration - duration)
            if delta <= 3.0:
                score += 3.0
            elif delta <= 10.0:
                score += 1.0

        if LRCLIBClient._candidate_text(candidate):
            score += 1.5

        return score

    async def _request_json(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        headers = {"User-Agent": self.user_agent}
        async with httpx.AsyncClient(
            timeout=15.0, headers=headers, follow_redirects=True
        ) as client:
            response = await client.get(url, params=params)
            if getattr(response, "status_code", None) == 404:
                return None
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 404:
                    return None
                raise
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

        try:
            match = await self._request_json("/api/get", params)
        except httpx.HTTPError:
            match = None

        if isinstance(match, dict):
            result = self._normalize_lyrics_result(
                match, artist_name, track_name, album_name, duration
            )
            if result is not None:
                return result
        elif isinstance(match, list):
            for candidate in match:
                if not isinstance(candidate, dict):
                    continue
                result = self._normalize_lyrics_result(
                    candidate, artist_name, track_name, album_name, duration
                )
                if result is not None:
                    return result

        clean_track = re.sub(r"\s*[\(\[].*?[\)\]]", "", track_name).strip()
        if clean_track and clean_track.casefold() != track_name.casefold():
            clean_params = dict(params)
            clean_params["track_name"] = clean_track
            try:
                clean_match = await self._request_json("/api/get", clean_params)
            except httpx.HTTPError:
                clean_match = None
            if isinstance(clean_match, dict):
                result = self._normalize_lyrics_result(
                    clean_match, artist_name, track_name, album_name, duration
                )
                if result is not None:
                    return result

        search_params: list[dict[str, Any]] = [
            {"track_name": track_name, "artist_name": artist_name},
        ]
        if clean_track and clean_track.casefold() != track_name.casefold():
            search_params.append({"track_name": clean_track, "artist_name": artist_name})
            search_params.append({"q": f"{artist_name} {clean_track}".strip()})
        search_params.append({"q": f"{artist_name} {track_name}".strip()})

        for search_params_item in search_params:
            try:
                search_results = await self._request_json("/api/search", search_params_item)
            except httpx.HTTPError:
                continue
            candidates = search_results if isinstance(search_results, list) else []
            if not candidates:
                continue

            best_candidate = max(
                candidates,
                key=lambda item: self._match_score(item, track_name, artist_name, duration),
                default=None,
            )
            if best_candidate is None:
                continue

            normalized = self._normalize_lyrics_result(
                best_candidate, artist_name, track_name, album_name, duration
            )
            if normalized is not None:
                return normalized

        return None

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
        text = synced_text or plain_text or candidate.get("lyrics") or candidate.get("text")
        if not text:
            return None

        title = LRCLIBClient._normalize(
            candidate.get("trackName")
            or candidate.get("track_name")
            or candidate.get("name")
            or candidate.get("title")
            or track_name
        )
        artist = LRCLIBClient._normalize(
            candidate.get("artistName")
            or candidate.get("artist_name")
            or candidate.get("artist")
            or artist_name
        )
        album = LRCLIBClient._normalize(
            candidate.get("albumName")
            or candidate.get("album_name")
            or candidate.get("album")
            or (album_name or "")
        )

        return LyricResult(
            text=str(text),
            synced=bool(synced_text),
            title=title,
            artist=artist,
            album=album,
            duration=LRCLIBClient._normalize_duration(
                candidate.get("duration") or candidate.get("duration_ms") or duration
            ),
            cover_url=candidate.get("coverArt")
            or candidate.get("cover_art")
            or candidate.get("albumArt")
            or candidate.get("cover_url")
            or None,
        )


__all__ = ["LRCLIBClient", "LyricResult"]
