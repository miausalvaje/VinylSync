"""API request models."""

from __future__ import annotations

from pydantic import BaseModel


class TrackOverride(BaseModel):
    """Payload for a manual track correction."""

    title: str
    artist: str
    album: str = ""
    duration: float | None = None
    synced_lyrics: str | None = None
    plain_lyrics: str | None = None
    cover_url: str | None = None
