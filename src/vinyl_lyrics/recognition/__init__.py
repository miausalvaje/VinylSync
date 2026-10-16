"""Track recognition providers."""

from __future__ import annotations

from typing import Any

from vinyl_lyrics.recognition.acrcloud import ACRCloudRecognizer
from vinyl_lyrics.recognition.audd import AudDRecognizer
from vinyl_lyrics.recognition.base import BaseRecognizer, Recognizer, Track
from vinyl_lyrics.recognition.fallback import FallbackRecognizer
from vinyl_lyrics.recognition.shazam import ShazamRecognizer


def _create_single_recognizer(
    provider: str, settings: dict[str, Any], timeout: float
) -> Recognizer:
    """Create one concrete recognition provider."""
    if provider == "shazam":
        return ShazamRecognizer()
    if provider == "acrcloud":
        return ACRCloudRecognizer(
            config=settings.get("acrcloud", {}),
            timeout=timeout,
        )
    if provider == "audd":
        raise ValueError(
            "AudD recognition is not implemented yet; use 'shazam' until the adapter is complete."
        )
    raise ValueError(f"Unsupported recognition provider '{provider}'.")


def create_recognizer(
    provider: str | None = None, config: dict[str, Any] | None = None
) -> Recognizer:
    """Create the primary recognition provider and configured fallbacks.

    recognition.fallback_providers is an optional ordered list. A fallback
    is tried when the preceding provider returns no match or raises an error.
    """
    settings = (config or {}).get("recognition", {})
    selected = (provider or settings.get("provider") or "shazam").lower()
    timeout = float(settings.get("timeout_seconds", 30.0))
    primary = _create_single_recognizer(selected, settings, timeout)

    fallback_names = settings.get("fallback_providers", [])
    if fallback_names is None:
        fallback_names = []
    if not isinstance(fallback_names, list) or any(
        not isinstance(name, str) or not name.strip() for name in fallback_names
    ):
        raise ValueError("recognition.fallback_providers must be a list of provider names.")

    if not fallback_names:
        return primary

    provider_names = [selected]
    providers: list[Recognizer] = [primary]
    for raw_name in fallback_names:
        fallback_name = raw_name.strip().lower()
        if fallback_name in provider_names:
            raise ValueError(
                f"Recognition provider '{fallback_name}' is configured more than once."
            )
        providers.append(_create_single_recognizer(fallback_name, settings, timeout))
        provider_names.append(fallback_name)

    return FallbackRecognizer(providers)


__all__ = [
    "ACRCloudRecognizer",
    "AudDRecognizer",
    "BaseRecognizer",
    "FallbackRecognizer",
    "Recognizer",
    "ShazamRecognizer",
    "Track",
    "create_recognizer",
]
