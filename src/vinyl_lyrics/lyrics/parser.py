"""LRC parser."""

from __future__ import annotations

import re

TIMESTAMP_RE = re.compile(
    r"\[(?P<minutes>\d{1,2}):(?P<seconds>\d{2})(?:[.:](?P<milliseconds>\d{1,3}))?\]"
)


def _parse_timestamp(timestamp: str) -> float:
    """Parse an LRC timestamp such as mm:ss.xx or mm:ss.xxx."""
    text = timestamp.strip("[]")
    match = re.fullmatch(
        r"(?P<minutes>\d{1,2}):(?P<seconds>\d{2})(?:[.:](?P<milliseconds>\d{1,3}))?", text
    )
    if match is None:
        raise ValueError(f"Invalid LRC timestamp: {timestamp!r}")

    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    milliseconds = match.group("milliseconds") or "0"
    fractional = milliseconds.ljust(3, "0")[:3]
    return minutes * 60.0 + seconds + (int(fractional) / 1000.0)


def parse_lrc(raw_lyric: str) -> list[tuple[float, str]]:
    """Convert LRC text into ordered (seconds, text) tuples."""
    parsed: list[tuple[float, str]] = []

    for raw_line in raw_lyric.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        timestamps = list(TIMESTAMP_RE.finditer(raw_line))
        if not timestamps:
            continue

        for index, match in enumerate(timestamps):
            start = match.end()
            end = timestamps[index + 1].start() if index + 1 < len(timestamps) else len(raw_line)
            segment = raw_line[start:end].strip()
            if segment and segment.startswith("[") and re.match(r"\[[A-Za-z]+:.*\]", segment):
                continue
            parsed.append((_parse_timestamp(match.group(0)), segment if segment else ""))

    return sorted(parsed, key=lambda pair: pair[0])


def extract_lrc_lines(raw_lyric: str) -> list[tuple[float, str]]:
    """Compatibility entry point for LRC parsing."""
    return parse_lrc(raw_lyric)


__all__ = ["parse_lrc", "extract_lrc_lines"]
