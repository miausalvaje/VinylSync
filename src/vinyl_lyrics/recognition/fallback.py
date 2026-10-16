"""Recognition chains with ordered fallback providers."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from vinyl_lyrics.recognition.base import BaseRecognizer, Recognizer, Track

logger = logging.getLogger(__name__)


def _provider_name(provider: Recognizer) -> str:
    """Return a readable provider name for logs."""
    return str(getattr(provider, "name", type(provider).__name__))


class FallbackRecognizer(BaseRecognizer):
    """Try recognition providers in order until one identifies a track."""

    name = "fallback"

    def __init__(self, providers: Sequence[Recognizer]) -> None:
        if not providers:
            raise ValueError("At least one recognition provider is required.")
        self.providers = tuple(providers)
        self.name = " -> ".join(_provider_name(provider) for provider in self.providers)

    async def recognize(self, wav_bytes: bytes) -> Track | None:
        """Return the first match; try the next provider on no-match or error."""
        last_error: Exception | None = None
        for index, provider in enumerate(self.providers):
            provider_name = _provider_name(provider)
            try:
                track = await provider.recognize(wav_bytes)
            except Exception as error:
                last_error = error
                if index + 1 < len(self.providers):
                    logger.warning(
                        "Recognition provider %s failed; falling back to %s: %s",
                        provider_name,
                        _provider_name(self.providers[index + 1]),
                        error,
                        exc_info=True,
                    )
                continue

            if track is not None:
                if index > 0:
                    logger.info("Track recognized by fallback provider %s.", provider_name)
                return track

            if index + 1 < len(self.providers):
                logger.info(
                    "Recognition provider %s returned no match; trying fallback provider %s.",
                    provider_name,
                    _provider_name(self.providers[index + 1]),
                )

        if last_error is not None:
            raise last_error
        return None


__all__ = ["FallbackRecognizer"]
