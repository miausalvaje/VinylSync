"""List available audio devices."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vinyl_lyrics.audio.devices import list_audio_sources  # noqa: E402


def main() -> None:
    sources = list_audio_sources()
    if not sources:
        print("No audio sources were detected.")
        return

    for source in sources:
        marker = " [default]" if source.get("default") else ""
        print(f"{source['source_id']}: {source['name']} ({source['type']}){marker}")


if __name__ == "__main__":
    main()
