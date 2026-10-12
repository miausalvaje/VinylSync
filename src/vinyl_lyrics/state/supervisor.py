"""Playback supervision and decision orchestration."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from vinyl_lyrics.audio.processing import SilenceState
from vinyl_lyrics.recognition.base import Track
from vinyl_lyrics.state.player import PlayerState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StartRecognition:
    """Request a recognition run."""

    since: float | None = None
    min_sample_seconds: float = 0.0
    background: bool = False
    cancel_current: bool = False


@dataclass(frozen=True, slots=True)
class BroadcastEvent:
    """Request an event broadcast."""

    event: str
    payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ApplyTrack:
    """Request applying a track to the player and loading lyrics."""

    track: Track
    locked: bool = False


SupervisorAction = StartRecognition | BroadcastEvent | ApplyTrack


class PlaybackSupervisor:
    """Supervise player state, silence transitions, and recognition scheduling."""

    def __init__(
        self,
        player: PlayerState,
        clock: Callable[[], float] = time.monotonic,
        idle_timeout_seconds: float = 20.0,
        min_sample_seconds: float = 5.0,
        track_gap_seconds: float = 1.5,
        break_max_seconds: float = 10.0,
        end_margin_seconds: float = 10.0,
        status: str = "idle",
    ) -> None:
        self.player = player
        self.clock = clock
        self.idle_timeout_seconds = float(idle_timeout_seconds)
        self.min_sample_seconds = float(min_sample_seconds)
        self.track_gap_seconds = float(track_gap_seconds)
        self.break_max_seconds = float(break_max_seconds)
        self.end_margin_seconds = float(end_margin_seconds)
        self.status = status
        self.locked = False
        self.failed_attempts = 0
        self.next_recognition_at = 0.0
        self.signal_resumed_at: float | None = None
        self.track_gap_pending = False
        self.silence_started_monotonic: float | None = None
        self.silence_type: str | None = None
        self._pending_candidate: Track | None = None
        self._pending_candidate_count: int = 0

    def handle_silence(
        self,
        silence: SilenceState,
        recognition_busy: bool,
    ) -> list[SupervisorAction]:
        """Handle silence detector updates and schedule playback transitions."""
        actions: list[SupervisorAction] = []
        now = self.clock()

        # 1. On silence transition: became_silent
        if silence.became_silent:
            self.silence_started_monotonic = now - silence.silent_for
            if self.player.current_track is not None:
                remaining = self.player.remaining_seconds(now)
                duration = self.player.current_track.duration
                if duration is not None and (
                    (remaining is not None and remaining <= self.end_margin_seconds)
                    or self.player.elapsed_seconds(now) >= duration
                ):
                    self.silence_type = "boundary"
                else:
                    self.silence_type = "break"
                logger.info(
                    "Silence classified as %s: remaining=%s, duration=%s",
                    self.silence_type,
                    f"{remaining:.1f}s" if remaining is not None else "unknown",
                    f"{duration:.1f}s" if duration is not None else "unknown",
                )

        # 2. Ongoing silence handling
        if not silence.active:
            if self.player.current_track is not None and self.status == "playing":
                if self.silence_type == "break":
                    if silence.silent_for >= self.break_max_seconds:
                        logger.info(
                            "Silence exceeded break_max_seconds (%.1fs). "
                            "Reclassifying as needle lift.",
                            self.break_max_seconds,
                        )
                        self.player.current_track = None
                        self.locked = False
                        self.silence_type = None
                        self.status = "idle"
                        actions.append(BroadcastEvent("idle"))
                    elif self.player.is_past_end(self.end_margin_seconds, now):
                        logger.info(
                            "Track elapsed past end with margin during silence. Discarding track."
                        )
                        self.player.current_track = None
                        self.locked = False
                        self.silence_type = None
                        self.status = "idle"
                        actions.append(BroadcastEvent("idle"))
                elif self.silence_type == "boundary":
                    if silence.silent_for >= self.idle_timeout_seconds:
                        self.player.current_track = None
                        self.locked = False
                        self.silence_type = None
                        self.status = "idle"
                        actions.append(BroadcastEvent("idle"))
            elif self.status == "listening" and silence.silent_for >= self.idle_timeout_seconds:
                self.status = "idle"
                actions.append(BroadcastEvent("idle"))

        # 3. On signal return: became_active
        if silence.became_active:
            self.signal_resumed_at = now
            if self.player.current_track is not None:
                if self.silence_type == "break":
                    self.silence_type = None
                    actions.append(
                        StartRecognition(
                            since=self.signal_resumed_at,
                            min_sample_seconds=self.min_sample_seconds,
                            background=True,
                            cancel_current=True,
                        )
                    )
                elif self.silence_type == "boundary":
                    self.player.current_track = None
                    self.locked = False
                    self.status = "listening"
                    self.silence_type = None
                    self.failed_attempts = 0
                    self.next_recognition_at = 0.0
                    self.track_gap_pending = True
                    actions.append(BroadcastEvent("idle"))
            else:
                self.failed_attempts = 0
                self.next_recognition_at = 0.0
                if self.status == "idle":
                    self.status = "listening"

        # 4. Sustained signal: schedule recognition
        if silence.active:
            if self.player.current_track is None and self.status in {"idle", "listening"}:
                if not recognition_busy and now >= self.next_recognition_at:
                    if self.signal_resumed_at is not None:
                        if now - self.signal_resumed_at >= self.min_sample_seconds:
                            actions.append(
                                StartRecognition(
                                    since=self.signal_resumed_at,
                                    min_sample_seconds=self.min_sample_seconds,
                                    background=False,
                                )
                            )
            elif (
                self.player.current_track is not None
                and self.status == "playing"
                and not self.locked
            ):
                if (
                    self.player.needs_reidentification(now)
                    and now >= self.next_recognition_at
                    and not recognition_busy
                ):
                    actions.append(StartRecognition(background=True))

        return actions

    def handle_recognition_start(self, background: bool = False) -> list[SupervisorAction]:
        """Record recognition start and emit identifying event when appropriate."""
        if not background and self.player.current_track is None:
            self.status = "identifying"
            return [BroadcastEvent("state")]
        return []

    def handle_recognition_no_snapshot(self, background: bool = False) -> list[SupervisorAction]:
        """Handle case when capture buffer does not have enough audio."""
        if self.player.current_track is None and not background:
            self.status = "listening"
        return []

    def handle_recognition_no_track(self, background: bool = False) -> list[SupervisorAction]:
        """Handle when recognizer returns no track."""
        now = self.clock()
        self.failed_attempts += 1
        retry_delays = (8.0, 16.0, 30.0)
        delay = retry_delays[min(self.failed_attempts - 1, len(retry_delays) - 1)]
        self.next_recognition_at = now + delay
        self._pending_candidate = None
        self._pending_candidate_count = 0

        if self.player.current_track is not None:
            return []

        self.status = "idle" if self.failed_attempts >= 3 else "listening"
        return [BroadcastEvent(self.status)]

    def handle_recognition_error(
        self,
        error: Exception,
        background: bool = False,
    ) -> list[SupervisorAction]:
        """Handle recognition exception."""
        now = self.clock()
        self.failed_attempts += 1
        retry_delays = (8.0, 16.0, 30.0)
        delay = retry_delays[min(self.failed_attempts - 1, len(retry_delays) - 1)]
        self.next_recognition_at = now + delay
        self._pending_candidate = None
        self._pending_candidate_count = 0

        if self.player.current_track is not None:
            return []

        self.status = "idle"
        return [BroadcastEvent("error", {"event": "error", "message": str(error)})]

    def handle_recognition_success(
        self,
        track: Track,
        background: bool = False,
    ) -> list[SupervisorAction]:
        """Handle successfully recognized track."""
        now = self.clock()
        current = self.player.current_track

        if current is None:
            self.failed_attempts = 0
            self.track_gap_pending = False
            self.signal_resumed_at = None
            self.status = "playing"
            self.next_recognition_at = now + self.player.reidentify_interval
            self._pending_candidate = None
            self._pending_candidate_count = 0
            return [
                ApplyTrack(track, locked=False),
                BroadcastEvent("track"),
            ]

        is_same = (
            current.artist.casefold() == track.artist.casefold()
            and current.title.casefold() == track.title.casefold()
        )

        if is_same:
            self._pending_candidate = None
            self._pending_candidate_count = 0
            self.failed_attempts = 0
            self.next_recognition_at = now + self.player.reidentify_interval
            if track.offset_seconds is not None:
                prev_offset = current.offset_seconds
                new_offset = self.player.adjust_offset_if_needed(track.offset_seconds, now=now)
                if abs(new_offset - prev_offset) > 1.5:
                    return [BroadcastEvent("state")]
            return []

        if self.locked:
            self._pending_candidate = None
            self._pending_candidate_count = 0
            return []

        is_same_candidate = (
            self._pending_candidate is not None
            and self._pending_candidate.artist.casefold() == track.artist.casefold()
            and self._pending_candidate.title.casefold() == track.title.casefold()
        )
        if is_same_candidate:
            self._pending_candidate_count += 1
        else:
            self._pending_candidate = track
            self._pending_candidate_count = 1

        if self._pending_candidate_count >= 2:
            self._pending_candidate = None
            self._pending_candidate_count = 0
            self.failed_attempts = 0
            self.status = "playing"
            self.next_recognition_at = now + self.player.reidentify_interval
            return [
                ApplyTrack(track, locked=False),
                BroadcastEvent("track"),
            ]

        logger.info(
            "Different track candidate detected (%s - %s), requesting confirmation sample",
            track.artist,
            track.title,
        )
        return [
            StartRecognition(
                since=now,
                min_sample_seconds=self.min_sample_seconds,
                background=True,
            )
        ]


__all__ = [
    "ApplyTrack",
    "BroadcastEvent",
    "PlaybackSupervisor",
    "StartRecognition",
    "SupervisorAction",
]
