"""Player state management."""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

# Recognition positions agree to a few hundredths of a second, but now and
# then a match lands a beat or two off (0.9-2 s) and the next one is right
# again. A position that disagrees with the clock by more than the tolerance is
# therefore held until a fresh sample confirms it (a skipped groove or an
# edited pressing); a lone outlier is dropped. The tolerance grows with the
# time since the last accepted position to leave room for a speed error.
_FIX_TOLERANCE_SECONDS = 0.3
_RATE_TOLERANCE = 0.005
# Differences up to this are measurement noise and leave the clock alone.
_NOISE_SECONDS = 0.05
# Accepted differences up to this are applied at once; larger ones halfway.
_SNAP_SECONDS = 0.15
_SMALL_CORRECTION_GAIN = 0.5
# Playback speed: turntables run a little off the reference recording.
_MAX_RATE_DEVIATION = 0.05
_MIN_RATE_SPAN_SECONDS = 30.0
_RATE_EPSILON = 1e-5


def _plausible_rate(rate: float | None) -> float | None:
    if rate is None or abs(rate - 1.0) > _MAX_RATE_DEVIATION:
        return None
    return rate


@dataclass(slots=True)
class PositionFix:
    """A recognized track position at a monotonic timestamp."""

    at: float
    position: float
    rate: float | None = None


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
    # Monotonic time at which position 0 played, at playback_rate.
    started_at_monotonic: float = field(default_factory=time.monotonic)
    playback_rate: float = 1.0
    fixes: list[PositionFix] = field(default_factory=list)
    pending_fix: PositionFix | None = None

    @property
    def started_at(self) -> float:
        return self.started_at_monotonic

    def position(self, now: float | None = None) -> float:
        """Return the unclamped track position at ``now``."""
        timestamp = time.monotonic() if now is None else now
        return (timestamp - self.started_at_monotonic) * self.playback_rate

    def set_position(self, position: float, now: float) -> None:
        """Move the clock so that ``position`` plays at ``now``."""
        self.started_at_monotonic = now - position / self.playback_rate

    def elapsed_seconds(self, now: float | None = None) -> float:
        """Return elapsed playback time since the song started."""
        return max(0.0, self.position(now))

    def lyric_elapsed_seconds(self, now: float | None = None) -> float:
        """Return elapsed time for lyric synchronization, including the user offset."""
        return max(0.0, self.elapsed_seconds(now) + self.manual_offset_seconds)

    def sync_offset(self, reference_now: float | None = None) -> None:
        """Adjust the start timestamp so that the current offset is preserved."""
        now = time.monotonic() if reference_now is None else reference_now
        self.set_position(self.offset_seconds, now)

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
        playback_rate: float | None = None,
    ) -> TrackState:
        """Set the current track and initialize its start time."""
        now = time.monotonic()
        rate = _plausible_rate(playback_rate)
        track = TrackState(
            title=title,
            artist=artist,
            album=album,
            duration=duration,
            cover_url=cover_url,
            offset_seconds=offset_seconds,
            manual_offset_seconds=manual_offset_seconds,
            playback_rate=rate or 1.0,
            fixes=[PositionFix(now, offset_seconds, rate)],
        )
        track.set_position(offset_seconds, now)
        self.current_track = track
        self.last_recheck_at = now
        return track

    def set_manual_offset(self, offset_seconds: float) -> None:
        """Apply a live manual lyric offset without changing capture position."""
        if self.current_track is not None:
            self.current_track.manual_offset_seconds = offset_seconds

    @property
    def needs_position_confirmation(self) -> bool:
        """Whether a position jump awaits confirmation by a fresh sample."""
        return self.current_track is not None and self.current_track.pending_fix is not None

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

    def adjust_offset_if_needed(
        self,
        detected_offset: float,
        now: float | None = None,
        playback_rate: float | None = None,
    ) -> float:
        """Reconcile the clock with a freshly recognized position.

        Returns the position the clock was set to, or the previous one when the
        detection was held for confirmation or was within measurement noise.
        """
        track = self.current_track
        if track is None:
            return 0.0

        timestamp = time.monotonic() if now is None else now
        self.last_recheck_at = timestamp
        fix = PositionFix(timestamp, detected_offset, _plausible_rate(playback_rate))
        current = track.position(timestamp)
        delta = detected_offset - current
        since_fix = max(0.0, timestamp - track.fixes[-1].at) if track.fixes else 0.0

        if abs(delta) <= _FIX_TOLERANCE_SECONDS + _RATE_TOLERANCE * since_fix:
            track.pending_fix = None
            track.fixes.append(fix)
            if abs(delta) <= _NOISE_SECONDS:
                target = None
            elif abs(delta) <= _SNAP_SECONDS:
                target = detected_offset
            else:
                target = current + delta * _SMALL_CORRECTION_GAIN
        elif track.pending_fix is not None and self._confirms(track, track.pending_fix, fix):
            # Two independent samples agree on the new position: a real jump.
            track.fixes = [track.pending_fix, fix]
            track.pending_fix = None
            target = detected_offset
        else:
            track.pending_fix = fix
            return track.offset_seconds

        position = current if target is None else target
        rate_changed = self._update_rate(track, position, timestamp)
        if target is None and not rate_changed:
            return track.offset_seconds
        track.offset_seconds = position
        track.set_position(position, timestamp)
        return position

    @staticmethod
    def _confirms(track: TrackState, pending: PositionFix, fix: PositionFix) -> bool:
        elapsed = fix.at - pending.at
        expected = pending.position + elapsed * track.playback_rate
        tolerance = _FIX_TOLERANCE_SECONDS + _RATE_TOLERANCE * elapsed
        return abs(fix.position - expected) <= tolerance

    @staticmethod
    def _update_rate(track: TrackState, position: float, now: float) -> bool:
        """Re-estimate the playback speed, keeping ``position`` continuous."""
        reported = [fix.rate for fix in track.fixes if fix.rate is not None]
        if reported:
            rate: float | None = statistics.median(reported)
        else:
            first, last = track.fixes[0], track.fixes[-1]
            span = last.at - first.at
            rate = None
            if span >= _MIN_RATE_SPAN_SECONDS:
                rate = _plausible_rate((last.position - first.position) / span)
        if rate is None or abs(rate - track.playback_rate) < _RATE_EPSILON:
            return False
        track.playback_rate = rate
        track.set_position(position, now)
        return True

    def reset_recheck_timer(self, now: float | None = None) -> None:
        """Reset the timer used for periodic re-identification."""
        self.last_recheck_at = time.monotonic() if now is None else now


__all__ = ["PlayerState", "PositionFix", "TrackState"]
