"""Player state management."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Recognition position estimates jitter by about a second between samples.
# Larger differences are real jumps (a skipped or edited section) and are
# applied at once; smaller ones are blended in, so the error of a biased first
# match fades over a few re-checks instead of persisting all track long.
_REBASE_THRESHOLD_SECONDS = 1.5
_MIN_CORRECTION_SECONDS = 0.25
_SMALL_CORRECTION_GAIN = 0.5


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
        return max(0.0, timestamp - self.started_at_monotonic)

    def lyric_elapsed_seconds(self, now: float | None = None) -> float:
        """Return elapsed time for lyric synchronization, including the user offset."""
        return max(0.0, self.elapsed_seconds(now) + self.manual_offset_seconds)

    def sync_offset(self, reference_now: float | None = None) -> None:
        """Adjust the start timestamp so that the current offset is preserved."""
        now = time.monotonic() if reference_now is None else reference_now
        self.started_at_monotonic = now - self.offset_seconds

    def remaining_seconds(self, now: float | None = None) -> float | None:
        """Return remaining playback time, or None if duration is unknown."""
        if self.duration is None:
            return None
        return self.duration - self.elapsed_seconds(now)

    def is_past_end(self, margin: float = 0.0, now: float | None = None) -> bool:
        """Return whether the track has played past its duration plus margin."""
        if self.duration is None:
            return False
        return self.elapsed_seconds(now) > (self.duration + margin)


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

    def lyric_elapsed_seconds(self, now: float | None = None) -> float:
        """Return the lyric timeline position for the current track."""
        if self.current_track is None:
            return 0.0
        return self.current_track.lyric_elapsed_seconds(now)

    def remaining_seconds(self, now: float | None = None) -> float | None:
        """Return the remaining playback time for the current track."""
        if self.current_track is None:
            return None
        return self.current_track.remaining_seconds(now)

    def is_past_end(self, margin: float = 0.0, now: float | None = None) -> bool:
        """Return whether the current track has played past its duration plus margin."""
        if self.current_track is None:
            return False
        return self.current_track.is_past_end(margin, now)

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
        delta = detected_offset - current_offset
        if abs(delta) > _REBASE_THRESHOLD_SECONDS:
            corrected = detected_offset
        elif abs(delta) >= _MIN_CORRECTION_SECONDS:
            corrected = current_offset + delta * _SMALL_CORRECTION_GAIN
        else:
            return self.current_track.offset_seconds
        self.current_track.offset_seconds = corrected
        self.current_track.started_at_monotonic = timestamp - corrected
        self.last_recheck_at = timestamp
        return corrected

    def reset_recheck_timer(self, now: float | None = None) -> None:
        """Reset the timer used for periodic re-identification."""
        self.last_recheck_at = time.monotonic() if now is None else now


__all__ = ["PlayerState", "TrackState"]
