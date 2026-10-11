"""Audio source discovery and resolution."""

from __future__ import annotations

import sys
from typing import Any

import sounddevice

from vinyl_lyrics.audio.source import AudioSourceInfo


def list_audio_sources() -> list[dict[str, Any]]:
    """List input and, when supported, Windows loopback sources."""
    sources: list[dict[str, Any]] = []
    for index, info in enumerate(sounddevice.query_devices()):
        if not isinstance(info, dict) or int(info.get("max_input_channels") or 0) <= 0:
            continue
        sources.append(
            {
                "source_id": f"input:{index}",
                "name": str(info.get("name") or f"Input {index}"),
                "type": "input",
                "default": index == int(sounddevice.default.device[0]),
            }
        )

    if sys.platform == "win32":
        try:
            import pyaudiowpatch

            audio = pyaudiowpatch.PyAudio()
            try:
                for index in range(audio.get_device_count()):
                    info = audio.get_device_info_by_index(index)
                    if info.get("isLoopbackDevice"):
                        sources.append(
                            {
                                "source_id": f"loopback:{index}",
                                "name": str(info.get("name") or f"Loopback {index}"),
                                "type": "loopback",
                                "default": False,
                            }
                        )
            finally:
                audio.terminate()
        except (ImportError, OSError, RuntimeError):
            pass
    return sources


def resolve_source(source_id: str | None) -> AudioSourceInfo:
    """Resolve a stable source identifier or the default source."""
    sources = list_audio_sources()
    if not sources:
        raise ValueError("No audio sources are available.")
    selected = (
        next(
            (source for source in sources if source["source_id"] == source_id),
            None,
        )
        if source_id
        else next((source for source in sources if source["default"]), sources[0])
    )
    if selected is None:
        raise ValueError(f"Audio source '{source_id}' was not found.")
    return AudioSourceInfo(
        source_id=selected["source_id"],
        name=selected["name"],
        source_type=selected["type"],
    )


def list_audio_devices() -> list[dict[str, Any]]:
    """Backward-compatible alias for input-device discovery."""
    return [source for source in list_audio_sources() if source["type"] == "input"]


def resolve_audio_device(device: str | int | None = None) -> int | None:
    """Resolve the legacy device selector to an input device index."""
    devices = list_audio_devices()
    if device is None:
        selected = next(
            (item for item in devices if item["default"]),
            devices[0] if devices else None,
        )
        return int(selected["source_id"].removeprefix("input:")) if selected else None
    candidate = str(device).strip()
    for item in devices:
        if (
            candidate == item["source_id"].removeprefix("input:")
            or candidate.casefold() in item["name"].casefold()
        ):
            return int(item["source_id"].removeprefix("input:"))
    raise ValueError(f"Audio device '{device}' was not found.")


__all__ = ["list_audio_devices", "list_audio_sources", "resolve_audio_device", "resolve_source"]
