"""AudD recognition provider."""

from __future__ import annotations

from vinyl_lyrics.recognition.base import BaseRecognizer, Track


class AudDRecognizer(BaseRecognizer):
    """Alternative AudD implementation with the same Recognizer interface."""

    name = "audd"

    async def recognize(self, wav_bytes: bytes) -> Track | None:
        """AudD implementation placeholder."""
        return None


__all__ = ["AudDRecognizer"]
