"""Shazam recognition provider."""

from __future__ import annotations

import io
import wave
from typing import Any

from vinyl_lyrics.recognition.base import BaseRecognizer, Track, coerce_float, normalize_cover_url

try:
    from shazamio import Shazam as ShazamClient
except ImportError:  # pragma: no cover - dependency may be unavailable in some environments.
    ShazamClient = None

# shazamio fingerprints only a centred slice of this length when the sample is
# longer, and Shazam reports the track position at the start of that slice: a
# 12 s sample is fingerprinted from 1 s to 11 s, so its offset is 1 s ahead of
# the sample start. Passed explicitly so the correction cannot drift from it.
_SIGNATURE_SECONDS = 10
# Turntables run a little off speed; anything beyond this is a bogus match.
_MAX_RATE_DEVIATION = 0.05


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    """Return the duration of a WAV payload, or 0.0 when it cannot be parsed."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
            rate = handle.getframerate()
            return handle.getnframes() / rate if rate > 0 else 0.0
    except (wave.Error, EOFError):
        return 0.0


def _signature_lead_in_seconds(wav_bytes: bytes) -> float:
    """Return where the fingerprinted slice starts inside the sample."""
    return max(0.0, (_wav_duration_seconds(wav_bytes) - _SIGNATURE_SECONDS) / 2.0)


class ShazamRecognizer(BaseRecognizer):
    """Shazam-based recognizer using shazamio when available."""

    name = "shazam"

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = float(timeout)
        self._client: Any = None

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
                if payload.get("offset_seconds") is not None
                else payload.get("offset")
                if payload.get("offset") is not None
                else payload.get("sample_offset")
            ),
        )

    @staticmethod
    def _first_match(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        matches = payload.get("matches")
        if not isinstance(matches, list) or not matches:
            return None
        match = matches[0]
        return match if isinstance(match, dict) else None

    @staticmethod
    def _extract_offset(payload: Any) -> float | None:
        """Extract Shazam's match offset: the track position of the fingerprint start."""
        match = ShazamRecognizer._first_match(payload)
        if match is None:
            # No position info: report unknown. A fabricated 0.0 here would
            # rebase the playback clock to the song start on every background
            # re-check, pinning synced lyrics near the first verse forever.
            return None
        return coerce_float(match.get("offset"))

    @staticmethod
    def _extract_rate(payload: Any) -> float | None:
        """Extract the playback speed relative to the reference recording.

        Shazam's ``timeskew`` is a ratio, not seconds: audio played 0.5 % fast
        reports about +0.005.
        """
        match = ShazamRecognizer._first_match(payload)
        if match is None:
            return None
        timeskew = coerce_float(match.get("timeskew"))
        if timeskew is None or abs(timeskew) > _MAX_RATE_DEVIATION:
            return None
        return 1.0 + timeskew

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = ShazamClient(segment_duration_seconds=_SIGNATURE_SECONDS)
        return self._client

    async def recognize_raw(self, wav_bytes: bytes) -> Any:
        """Return the provider response for diagnostics and normalization."""
        if ShazamClient is None:
            raise RuntimeError(
                "shazamio is not installed; install project dependencies to enable recognition."
            )

        client = self._get_client()
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
        self.last_raw_result = result
        return result

    async def recognize(self, wav_bytes: bytes) -> Track | None:
        result = await self.recognize_raw(wav_bytes)
        if result is None:
            return None

        payload = self._extract_track(result)
        if payload is None:
            return None

        if isinstance(payload, dict):
            offset = self._extract_offset(result)
            rate = self._extract_rate(result)
            if offset is not None:
                # Report the position at the start of the sample, which is
                # what the caller timestamps.
                lead_in = _signature_lead_in_seconds(wav_bytes)
                payload = {**payload, "offset_seconds": offset - lead_in * (rate or 1.0)}
            track = self._build_track(payload)
            if track is not None:
                track.playback_rate = rate
                return track

        return None


__all__ = ["ShazamRecognizer"]
