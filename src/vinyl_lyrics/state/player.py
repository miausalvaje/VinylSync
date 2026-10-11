"""Player state management."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class TrackState:
    """State for the currently active track."""

    title: str
    artist: str
    album: str = ""
    duration: float | None = None
    cover_url: str | None = None
    offset_seconds: float = 0.0
    manual_offset_seconds: float = 0.0
    started_at_monotonic: float = field(default_factory=time.monotonic)

    @property
    def started_at(self) -> float:
        return self.started_at_monotonic

    def elapsed_seconds(self, now: float | None = None) -> float:
        """Return elapsed playback time since the song started."""
        timestamp = time.monotonic() if now is None else now
        return max(0.0, timestamp - self.started_at_monotonic + self.manual_offset_seconds)

    def sync_offset(self, reference_now: float | None = None) -> None:
        """Adjust the start timestamp so that the current offset is preserved."""
        now = time.monotonic() if reference_now is None else reference_now
        self.started_at_monotonic = now - self.offset_seconds


@dataclass(slots=True)
class PlayerState:
    """Maintain the current recognized song and its sync state."""

    current_track: TrackState | None = None
    reidentify_interval: float = 45.0
    last_recheck_at: float = field(default_factory=time.monotonic)

    def set_track(
        self,
        title: str,
        artist: str,
        album: str = "",
        duration: float | None = None,
        cover_url: str | None = None,
        offset_seconds: float = 0.0,
        manual_offset_seconds: float = 0.0,
    ) -> TrackState:
        """Set the current track and initialize its start time."""
        now = time.monotonic()
        track = TrackState(
            title=title,
            artist=artist,
            album=album,
            duration=duration,
            cover_url=cover_url,
            offset_seconds=offset_seconds,
            manual_offset_seconds=manual_offset_seconds,
            started_at_monotonic=now - offset_seconds,
        )
        self.current_track = track
        self.last_recheck_at = now
        return track

    def set_manual_offset(self, offset_seconds: float) -> None:
        """Apply a live manual lyric offset without changing capture position."""
        if self.current_track is not None:
            self.current_track.manual_offset_seconds = offset_seconds

    def elapsed_seconds(self, now: float | None = None) -> float:
        """Return the elapsed playback time for the current track."""
        if self.current_track is None:
            return 0.0
        return self.current_track.elapsed_seconds(now)

    def needs_reidentification(self, now: float | None = None) -> bool:
        """Whether the current track should be re-identified."""
        timestamp = time.monotonic() if now is None else now
        return (
            self.current_track is not None
            and (timestamp - self.last_recheck_at) >= self.reidentify_interval
        )

    def adjust_offset_if_needed(self, detected_offset: float, now: float | None = None) -> float:
        """Rebase the current track when the detector reports a different offset."""
        if self.current_track is None:
            return 0.0

        timestamp = time.monotonic() if now is None else now
        current_offset = timestamp - self.current_track.started_at_monotonic
        delta = abs(detected_offset - current_offset)
        if delta > 1.5:
            self.current_track.offset_seconds = detected_offset
            self.current_track.started_at_monotonic = timestamp - detected_offset
            self.last_recheck_at = timestamp
            return detected_offset
        return self.current_track.offset_seconds

    def reset_recheck_timer(self, now: float | None = None) -> None:
        """Reset the timer used for periodic re-identification."""
        self.last_recheck_at = time.monotonic() if now is None else now


__all__ = ["PlayerState", "TrackState"]
