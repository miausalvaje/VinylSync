"""List available audio devices."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vinyl_lyrics.audio.devices import list_audio_devices  # noqa: E402


def main() -> None:
    devices = list_audio_devices()
    if not devices:
        print("No input-capable audio devices were detected.")
        return

    for device in devices:
        marker = " [default]" if device["default"] else ""
        print(f"{device['index']}: {device['name']} ({device['max_input_channels']} in){marker}")


if __name__ == "__main__":
    main()
