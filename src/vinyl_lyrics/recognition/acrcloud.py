"""ACRCloud recognition provider."""

from __future__ import annotations

from vinyl_lyrics.recognition.base import BaseRecognizer, Track


class ACRCloudRecognizer(BaseRecognizer):
    """Alternative ACRCloud implementation with the same Recognizer interface."""

    name = "acrcloud"

    async def recognize(self, wav_bytes: bytes) -> Track | None:
        """ACRCloud implementation placeholder."""
        return None


__all__ = ["ACRCloudRecognizer"]
