"""Multi-provider lyrics aggregator with automatic fallback."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from vinyl_lyrics.lyrics.kugou import KugouLyricsClient
from vinyl_lyrics.lyrics.lrclib import LRCLIBClient, LyricResult
from vinyl_lyrics.lyrics.parser import has_implausible_timing

logger = logging.getLogger(__name__)


class MultiProviderLyricsClient:
    """Orchestrate queries across LRCLIB and Kugou providers."""

    def __init__(
        self,
        lrclib_client: LRCLIBClient | None = None,
        kugou_client: KugouLyricsClient | None = None,
    ) -> None:
        self.lrclib = lrclib_client or LRCLIBClient()
        self.kugou = kugou_client or KugouLyricsClient()

    async def get_lyrics(
        self,
        artist_name: str,
        track_name: str,
        album_name: str | None = None,
        duration: float | None = None,
    ) -> LyricResult | None:
        """Query LRCLIB first; fall back to Kugou when it has nothing or bad timing.

        Mistimed LRCLIB lyrics are still returned when Kugou has no better
        synced version.
        """
        result: LyricResult | None = None
        try:
            result = await self.lrclib.get_lyrics(artist_name, track_name, album_name, duration)
        except Exception as error:
            logger.warning("LRCLIB lookup failed for %s - %s: %s", artist_name, track_name, error)

        if result is not None:
            if not (result.synced and has_implausible_timing(result.text)):
                return result
            logger.info(
                "LRCLIB synced lyrics for %s - %s look mistimed. Trying Kugou fallback...",
                artist_name,
                track_name,
            )
        else:
            logger.info(
                "LRCLIB has no lyrics for %s - %s. Trying Kugou fallback...",
                artist_name,
                track_name,
            )
        try:
            kugou_result = await self.kugou.get_lyrics(
                artist_name, track_name, album_name, duration
            )
            if kugou_result is not None and not has_implausible_timing(kugou_result.text):
                return kugou_result
        except Exception as error:
            logger.warning("Kugou lookup failed for %s - %s: %s", artist_name, track_name, error)

        return result

    async def search(self, query: str) -> list[dict[str, Any]]:
        """Combine search results from both providers for manual track correction."""
        lrclib_task = asyncio.create_task(self._safe_lrclib_search(query))
        kugou_task = asyncio.create_task(self._safe_kugou_search(query))
        lrclib_results, kugou_results = await asyncio.gather(lrclib_task, kugou_task)

        # Merge and deduplicate by lowercased title + artist
        combined: list[dict[str, Any]] = list(lrclib_results)
        seen = {
            (item.get("title", "").casefold(), item.get("artist", "").casefold())
            for item in lrclib_results
        }

        for item in kugou_results:
            key = (item.get("title", "").casefold(), item.get("artist", "").casefold())
            if key not in seen:
                seen.add(key)
                combined.append(item)

        return combined

    async def _safe_lrclib_search(self, query: str) -> list[dict[str, Any]]:
        try:
            return await self.lrclib.search(query)
        except Exception as error:
            logger.warning("LRCLIB search error: %s", error)
            return []

    async def _safe_kugou_search(self, query: str) -> list[dict[str, Any]]:
        try:
            candidates = await self.kugou.search(query)

            # Populate syncedLyrics for top 4 Kugou candidates
            async def fetch_content(c: dict[str, Any]) -> dict[str, Any]:
                try:
                    text = await self.kugou._download_lyrics(c["id"], c["accesskey"])
                    c["syncedLyrics"] = text
                    c["plainLyrics"] = None
                except Exception:
                    c["syncedLyrics"] = None
                return c

            tasks = [fetch_content(c) for c in candidates[:4]]
            if tasks:
                await asyncio.gather(*tasks)
            return candidates
        except Exception as error:
            logger.warning("Kugou search error: %s", error)
            return []
