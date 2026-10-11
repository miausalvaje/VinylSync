"""Shazam recognition provider."""

from __future__ import annotations

from typing import Any

from vinyl_lyrics.recognition.base import BaseRecognizer, Track, coerce_float, normalize_cover_url

try:
    from shazamio import Shazam as ShazamClient
except ImportError:  # pragma: no cover - dependency may be unavailable in some environments.
    ShazamClient = None


class ShazamRecognizer(BaseRecognizer):
    """Shazam-based recognizer using shazamio when available."""

    name = "shazam"

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = float(timeout)

    @staticmethod
    def _extract_track(payload: Any) -> dict[str, Any] | None:
        if payload is None:
            return None

        if isinstance(payload, dict):
            if "title" in payload or "artist" in payload:
                return payload
            for key in ("track", "metadata", "result", "song"):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    extracted = ShazamRecognizer._extract_track(nested)
                    if extracted:
                        return extracted
            if "matches" in payload and isinstance(payload["matches"], list):
                for item in payload["matches"]:
                    if isinstance(item, dict):
                        nested = ShazamRecognizer._extract_track(item)
                        if nested:
                            return nested
            return None

        if isinstance(payload, list):
            for item in payload:
                track = ShazamRecognizer._extract_track(item)
                if track:
                    return track
            return None

        if hasattr(payload, "track"):
            return ShazamRecognizer._extract_track(payload.track)

        if hasattr(payload, "title") or hasattr(payload, "artist"):
            return {
                "title": getattr(payload, "title", None),
                "artist": getattr(payload, "artist", None),
                "album": getattr(payload, "album", None),
                "duration": getattr(payload, "duration", None),
                "cover_url": getattr(payload, "cover_url", None),
                "offset_seconds": getattr(payload, "offset_seconds", None),
            }

        return None

    @staticmethod
    def _build_track(payload: dict[str, Any]) -> Track | None:
        title = payload.get("title") or payload.get("track_title") or payload.get("name")
        artist = payload.get("artist") or payload.get("subtitle") or payload.get("artist_name")
        album = payload.get("album") or payload.get("album_name") or ""
        if not title or not artist:
            return None

        return Track(
            title=str(title),
            artist=str(artist),
            album=str(album) if album else "",
            duration=coerce_float(
                payload.get("duration") or payload.get("track_duration") or payload.get("seconds")
            ),
            cover_url=normalize_cover_url(
                payload.get("cover_url")
                or payload.get("coverart")
                or payload.get("images")
                or payload.get("artwork")
            ),
            offset_seconds=coerce_float(
                payload.get("offset_seconds")
                or payload.get("offset")
                or payload.get("sample_offset")
            ),
        )

    async def recognize(self, wav_bytes: bytes) -> Track | None:
        if ShazamClient is None:
            raise RuntimeError(
                "shazamio is not installed; install project dependencies to enable recognition."
            )

        client = ShazamClient()
        result: Any = None
        method_names = ("recognize", "identify", "recognize_song")
        for method_name in method_names:
            method = getattr(client, method_name, None)
            if not callable(method):
                continue
            try:
                result = await method(wav_bytes)
                break
            except TypeError:
                continue

        if result is None:
            return None

        payload = self._extract_track(result)
        if payload is None:
            return None

        if isinstance(payload, dict):
            track = self._build_track(payload)
            if track is not None:
                return track

        return None


__all__ = ["ShazamRecognizer"]
