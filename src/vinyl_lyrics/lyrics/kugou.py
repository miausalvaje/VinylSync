"""Kugou lyrics provider implementation."""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

import httpx

from vinyl_lyrics.lyrics.lrclib import LyricResult

logger = logging.getLogger(__name__)


class KugouLyricsClient:
    """Asynchronous client for Kugou's public lyrics search and download API."""

    SEARCH_URL = "http://lyrics.kugou.com/search"
    DOWNLOAD_URL = "http://lyrics.kugou.com/download"

    def __init__(self, timeout: float = 8.0, user_agent: str = "VinylSync/0.1.0") -> None:
        self.timeout = timeout
        self.headers = {"User-Agent": user_agent}

    @staticmethod
    def _clean_string(value: str) -> str:
        """Strip parenthesized/bracketed tags and extra whitespace."""
        cleaned = re.sub(r"\s*[\(\[].*?[\)\]]", "", value)
        cleaned = re.split(r"\s*-\s*", cleaned)[0]
        return cleaned.strip()

    @staticmethod
    def _extract_primary_artist(artist: str) -> str:
        """Extract the lead artist name before featured artists."""
        parts = re.split(r"\s*(?:&|,|feat\.|ft\.|with|vs\.)\s*", artist, flags=re.IGNORECASE)
        return parts[0].strip() if parts else artist.strip()

    async def get_lyrics(
        self,
        artist_name: str,
        track_name: str,
        album_name: str | None = None,
        duration: float | None = None,
    ) -> LyricResult | None:
        """Search and retrieve timestamped LRC lyrics from Kugou."""
        clean_track = self._clean_string(track_name)
        primary_artist = self._extract_primary_artist(artist_name)

        search_queries = [
            f"{artist_name} - {clean_track}",
            f"{primary_artist} - {clean_track}",
            f"{artist_name} - {track_name}",
            clean_track,
        ]
        seen_queries: set[str] = set()

        for query in search_queries:
            q = query.strip()
            if not q or q.casefold() in seen_queries:
                continue
            seen_queries.add(q.casefold())

            candidate = await self._find_best_candidate(q, artist_name, clean_track, duration)
            if candidate is not None:
                lrc_text = await self._download_lyrics(candidate["id"], candidate["accesskey"])
                if lrc_text and lrc_text.strip():
                    logger.info("Found synced lyrics on Kugou for %s - %s", artist_name, track_name)
                    return LyricResult(
                        text=lrc_text.strip(),
                        synced=True,
                        title=candidate.get("song") or track_name,
                        artist=candidate.get("singer") or artist_name,
                        album=album_name or "",
                        duration=duration,
                    )

        return None

    async def _find_best_candidate(
        self,
        keyword: str,
        artist_name: str,
        track_name: str,
        duration: float | None = None,
    ) -> dict[str, Any] | None:
        """Query Kugou search endpoint and return the highest-scoring candidate."""
        params: dict[str, Any] = {
            "ver": 1,
            "man": "yes",
            "client": "pc",
            "keyword": keyword,
        }
        if duration is not None:
            params["duration"] = int(round(duration * 1000))

        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                response = await client.get(self.SEARCH_URL, params=params)
                if response.status_code != 200:
                    return None
                data = response.json()
        except (httpx.HTTPError, OSError, ValueError):
            return None

        candidates = data.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            return None

        def score(item: dict[str, Any]) -> float:
            pts = 0.0
            song = str(item.get("song") or "").casefold()
            singer = str(item.get("singer") or "").casefold()
            target_track = track_name.casefold()
            target_artist = artist_name.casefold()

            if target_track in song or song in target_track:
                pts += 5.0
            if target_artist in singer or singer in target_artist:
                pts += 4.0

            cand_dur = item.get("duration")
            if duration is not None and cand_dur is not None:
                diff_sec = abs(float(cand_dur) / 1000.0 - duration)
                if diff_sec <= 3.0:
                    pts += 4.0
                elif diff_sec <= 8.0:
                    pts += 2.0

            return pts

        best = max(candidates, key=score, default=None)
        return best if best and score(best) >= 4.0 else (candidates[0] if candidates else None)

    async def _download_lyrics(self, candidate_id: Any, access_key: str) -> str | None:
        """Download and base64-decode the raw LRC string."""
        params = {
            "ver": 1,
            "client": "pc",
            "id": candidate_id,
            "accesskey": access_key,
            "fmt": "lrc",
            "charset": "utf8",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                response = await client.get(self.DOWNLOAD_URL, params=params)
                if response.status_code != 200:
                    return None
                payload = response.json()
                raw_content = payload.get("content")
                if not raw_content:
                    return None
                decoded = base64.b64decode(raw_content).decode("utf-8", errors="replace")
                return decoded
        except (httpx.HTTPError, OSError, ValueError):
            return None

    async def search(self, query: str) -> list[dict[str, Any]]:
        """Search candidates for manual correction dialog."""
        params: dict[str, str | int] = {
            "ver": 1,
            "man": "yes",
            "client": "pc",
            "keyword": query,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                response = await client.get(self.SEARCH_URL, params=params)
                if response.status_code != 200:
                    return []
                candidates = response.json().get("candidates", [])
        except (httpx.HTTPError, OSError, ValueError):
            return []

        results: list[dict[str, Any]] = []
        for item in candidates[:8]:
            dur_ms = item.get("duration")
            dur_sec = round(float(dur_ms) / 1000.0, 1) if dur_ms else None
            results.append(
                {
                    "id": item.get("id"),
                    "title": item.get("song") or "",
                    "artist": item.get("singer") or "",
                    "album": "Kugou Music",
                    "duration": dur_sec,
                    "synced": True,
                    "accesskey": item.get("accesskey"),
                    "source": "kugou",
                }
            )
        return results
