"""ACRCloud audio recognition provider."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from vinyl_lyrics.recognition.base import (
    BaseRecognizer,
    Track,
    coerce_float,
    normalize_cover_url,
)

_IDENTIFY_PATH = "/v1/identify"
_MAX_SAMPLE_BYTES = 5 * 1024 * 1024
_DEEZER_ALBUM_URL = "https://api.deezer.com/album"
_COVER_TIMEOUT_SECONDS = 5.0

logger = logging.getLogger(__name__)


class ACRCloudRecognizer(BaseRecognizer):
    """Identify short audio samples through the ACRCloud Identify API."""

    name = "acrcloud"

    def __init__(self, config: dict[str, Any] | None = None, timeout: float = 30.0) -> None:
        settings = config or {}
        raw_host = str(settings.get("host") or "").strip()
        self.access_key = str(settings.get("access_key") or "").strip()
        self.access_secret = str(settings.get("access_secret") or "").strip()
        self.timeout = float(timeout)

        missing = [
            name
            for name, value in (
                ("ACR_HOST", raw_host),
                ("ACR_ACCESS_KEY", self.access_key),
                ("ACR_ACCESS_SECRET", self.access_secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "ACRCloud is selected but required credentials are missing: "
                + ", ".join(missing)
                + ". Set them in .env or recognition.acrcloud in config.yaml."
            )
        if self.timeout <= 0:
            raise ValueError("ACRCloud timeout must be positive.")

        # ACRCloud project hosts are commonly supplied as a hostname, but
        # accepting a complete HTTPS URL makes configuration less surprising.
        parsed = urlsplit(raw_host if "://" in raw_host else f"https://{raw_host}")
        if parsed.scheme.lower() != "https":
            raise ValueError("ACRCloud host must use HTTPS.")
        if (
            not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("ACR_HOST must be an HTTPS host name without a path or query.")
        self.base_url = f"https://{parsed.netloc}"

    @staticmethod
    def _build_signature(access_key: str, access_secret: str, timestamp: str) -> str:
        """Build the ACRCloud Identify API v1 HMAC-SHA1 signature."""
        string_to_sign = "\n".join(("POST", _IDENTIFY_PATH, access_key, "audio", "1", timestamp))
        digest = hmac.new(
            access_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("ascii")

    @staticmethod
    def _extract_cover_url(payload: dict[str, Any]) -> str | None:
        """Extract artwork from optional, nested external metadata."""
        direct = normalize_cover_url(
            payload.get("cover_url")
            or payload.get("coverart")
            or payload.get("images")
            or payload.get("artwork")
        )
        if direct:
            return direct

        external = payload.get("external_metadata")
        if not isinstance(external, dict):
            return None
        for provider in external.values():
            if isinstance(provider, dict):
                album = provider.get("album")
                if isinstance(album, dict):
                    cover = normalize_cover_url(album.get("images"))
                    if cover:
                        return cover
                cover = normalize_cover_url(provider)
                if cover:
                    return cover
        return None

    @staticmethod
    def _extract_sample_start(payload: dict[str, Any]) -> float | None:
        """Return the song position matching the *start* of the sample, in seconds.

        The runtime adds the time elapsed since the sample started, as Shazam
        reports the sample start. ACRCloud's play_offset_ms points at the end of
        the matched audio instead, so using it directly runs lyrics one whole
        recognition window (~12 s) ahead.
        """
        db_begin_ms = coerce_float(payload.get("db_begin_time_offset_ms"))
        if db_begin_ms is not None:
            sample_begin_ms = coerce_float(payload.get("sample_begin_time_offset_ms"), 0.0) or 0.0
            return max(0.0, (db_begin_ms - sample_begin_ms) / 1000.0)

        play_offset_ms = coerce_float(payload.get("play_offset_ms"))
        if play_offset_ms is not None:
            sample_end_ms = coerce_float(payload.get("sample_end_time_offset_ms"), 0.0) or 0.0
            return max(0.0, (play_offset_ms - sample_end_ms) / 1000.0)

        return coerce_float(payload.get("offset_seconds"))

    @staticmethod
    def _deezer_album_id(payload: dict[str, Any]) -> str | None:
        external = payload.get("external_metadata")
        deezer = external.get("deezer") if isinstance(external, dict) else None
        album = deezer.get("album") if isinstance(deezer, dict) else None
        album_id = str(album.get("id") or "").strip() if isinstance(album, dict) else ""
        return album_id if album_id.isdigit() else None

    async def _fetch_deezer_cover(self, payload: dict[str, Any]) -> str | None:
        """Look up album artwork on Deezer; Identify results carry only its IDs."""
        album_id = self._deezer_album_id(payload)
        if album_id is None:
            return None
        try:
            async with httpx.AsyncClient(timeout=_COVER_TIMEOUT_SECONDS) as client:
                response = await client.get(f"{_DEEZER_ALBUM_URL}/{album_id}")
                response.raise_for_status()
                album: Any = response.json()
        except (httpx.HTTPError, ValueError) as error:
            logger.info("Deezer artwork lookup failed for album %s: %s", album_id, error)
            return None
        if not isinstance(album, dict):
            return None
        return normalize_cover_url(album.get("cover_xl") or album.get("cover_big"))

    @classmethod
    def _build_track(cls, payload: dict[str, Any]) -> Track | None:
        """Normalize an ACRCloud music metadata entry into VinylSync's Track."""
        title = payload.get("title") or payload.get("track_title")
        artists = payload.get("artists") or payload.get("artist") or []
        if isinstance(artists, str):
            artist = artists.strip()
        elif isinstance(artists, list):
            artist_names = [
                str(item.get("name")).strip()
                for item in artists
                if isinstance(item, dict) and item.get("name")
            ]
            artist = ", ".join(artist_names)
        else:
            artist = ""

        if not title or not artist:
            return None

        raw_album = payload.get("album")
        album = raw_album.get("name", "") if isinstance(raw_album, dict) else raw_album or ""
        duration_ms = coerce_float(payload.get("duration_ms"))
        duration = (
            duration_ms / 1000.0
            if duration_ms is not None
            else coerce_float(payload.get("duration"))
        )
        offset_seconds = cls._extract_sample_start(payload)

        return Track(
            title=str(title).strip(),
            artist=artist,
            album=str(album).strip() if album else "",
            duration=duration,
            cover_url=cls._extract_cover_url(payload),
            offset_seconds=offset_seconds,
        )

    async def recognize(self, wav_bytes: bytes) -> Track | None:
        """Upload a WAV sample to ACRCloud and return its best music match."""
        if not wav_bytes:
            return None
        if len(wav_bytes) > _MAX_SAMPLE_BYTES:
            raise ValueError("ACRCloud audio sample exceeds the 5 MiB Identify API size limit.")

        timestamp = str(int(time.time()))
        signature = self._build_signature(self.access_key, self.access_secret, timestamp)
        form = {
            "access_key": self.access_key,
            "sample_bytes": str(len(wav_bytes)),
            "timestamp": timestamp,
            "signature": signature,
            "data_type": "audio",
            "signature_version": "1",
        }
        files = {"sample": ("vinylsync.wav", wav_bytes, "audio/wav")}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}{_IDENTIFY_PATH}",
                data=form,
                files=files,
            )
            response.raise_for_status()
            payload: Any = response.json()

        if not isinstance(payload, dict):
            raise RuntimeError("ACRCloud returned an invalid response.")

        status = payload.get("status")
        if isinstance(status, dict):
            code = status.get("code")
            if code is not None and code != 0:
                # ACRCloud uses code 1001 to indicate that no result was found.
                if code == 1001:
                    return None
                message = str(status.get("msg") or "Unknown provider error")
                raise RuntimeError(f"ACRCloud recognition failed (code {code}): {message}")

        metadata = payload.get("metadata")
        music = metadata.get("music") if isinstance(metadata, dict) else None
        if not isinstance(music, list):
            return None
        for item in music:
            if isinstance(item, dict):
                track = self._build_track(item)
                if track is not None:
                    if track.cover_url is None:
                        track.cover_url = await self._fetch_deezer_cover(item)
                    return track
        return None


__all__ = ["ACRCloudRecognizer"]
