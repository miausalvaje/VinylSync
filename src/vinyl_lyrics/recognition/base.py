"""Recognition contracts and track models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class Track:
    """Normalized track metadata returned by a recognition provider."""

    title: str
    artist: str
    album: str = ""
    duration: float | None = None
    cover_url: str | None = None
    offset_seconds: float | None = None
    # Track seconds advanced per second of capture (turntable speed relative to
    # the reference recording), when the provider measures it.
    playback_rate: float | None = None


@runtime_checkable
class Recognizer(Protocol):
    """Async recognition interface implemented by all providers."""

    async def recognize(self, wav_bytes: bytes) -> Track | None: ...


class BaseRecognizer(ABC):
    """Common abstract base for recognition providers."""

    name: str = "base"

    @abstractmethod
    async def recognize(self, wav_bytes: bytes) -> Track | None:
        """Analyze an audio chunk and return the most probable track."""


def coerce_float(value: Any, default: float | None = None) -> float | None:
    """Convert ambiguous numeric values from provider payloads."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return float(stripped)
        except ValueError:
            return default
    return default


def normalize_cover_url(raw: Any) -> str | None:
    """Best-effort extraction of a URL-like cover value."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw if raw.startswith(("http://", "https://")) else None
    if isinstance(raw, (list, tuple)):
        for item in raw:
            value = normalize_cover_url(item)
            if value:
                return value
        return None
    if isinstance(raw, dict):
        for key in ("url", "cover_url", "coverart", "image", "artwork", "thumbnail"):
            value = normalize_cover_url(raw.get(key))
            if value:
                return value
        for key in ("images", "image_url", "cover"):
            value = normalize_cover_url(raw.get(key))
            if value:
                return value
    return None


__all__ = ["BaseRecognizer", "Recognizer", "Track", "coerce_float", "normalize_cover_url"]
