"""Track recognition providers."""

from __future__ import annotations

from typing import Any

from vinyl_lyrics.recognition.acrcloud import ACRCloudRecognizer
from vinyl_lyrics.recognition.audd import AudDRecognizer
from vinyl_lyrics.recognition.base import BaseRecognizer, Recognizer, Track
from vinyl_lyrics.recognition.shazam import ShazamRecognizer


def create_recognizer(
    provider: str | None = None, config: dict[str, Any] | None = None
) -> Recognizer:
    """Create the configured provider implementation."""
    settings = (config or {}).get("recognition", {}) if config else {}
    selected = (provider or settings.get("provider") or "shazam").lower()

    if selected == "shazam":
        return ShazamRecognizer()
    if selected == "acrcloud":
        raise ValueError(
            "ACRCloud recognition is not implemented yet; use 'shazam' "
            "until the adapter is complete."
        )
    if selected == "audd":
        raise ValueError(
            "AudD recognition is not implemented yet; use 'shazam' "
            "until the adapter is complete."
        )

    raise ValueError(f"Unsupported recognition provider '{selected}'.")


__all__ = [
    "ACRCloudRecognizer",
    "AudDRecognizer",
    "BaseRecognizer",
    "Recognizer",
    "ShazamRecognizer",
    "Track",
    "create_recognizer",
]
