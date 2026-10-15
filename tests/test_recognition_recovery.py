"""Regression tests: the backend must never silently stop detecting songs.

Covers the reported failure mode (first song detected, second song after a
reload never identified) and the slow stop-detection tuning:

- A hung recognition provider must time out into the normal backoff path
  instead of wedging all future detection.
- A flaky mid-play miss must not reset a half-confirmed track change.
- A stale "identifying" status with nothing in flight must reschedule.
- A runtime created via the sync REST path must still run its control loop.
"""

from __future__ import annotations

import asyncio
import time

from vinyl_lyrics.audio.capture import RecognitionSnapshot
from vinyl_lyrics.audio.processing import SilenceState
from vinyl_lyrics.main import ApplicationRuntime
from vinyl_lyrics.recognition.base import Track
from vinyl_lyrics.server.session import SessionManager
from vinyl_lyrics.state.player import PlayerState
from vinyl_lyrics.state.supervisor import (
    PlaybackSupervisor,
    StartRecognition,
)


class FakeClock:
    """Simulated monotonic clock."""

    def __init__(self, initial: float = 1000.0) -> None:
        self.current = initial

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> float:
        self.current += seconds
        return self.current


class FakeCapture:
    def recognition_snapshot(
        self,
        seconds: float = 8.0,
        since: float | None = None,
        min_seconds: float = 0.0,
    ) -> RecognitionSnapshot | None:
        del since, min_seconds
        return RecognitionSnapshot(b"wav", time.monotonic() - seconds, seconds)


class HangingRecognizer:
    """Simulates a provider whose network call never returns."""

    def __init__(self) -> None:
        self.calls = 0

    async def recognize(self, wav_bytes: bytes) -> Track | None:
        del wav_bytes
        self.calls += 1
        await asyncio.sleep(3600.0)
        return None


class EmptyRecognizer:
    def __init__(self) -> None:
        self.calls = 0

    async def recognize(self, wav_bytes: bytes) -> Track | None:
        del wav_bytes
        self.calls += 1
        return None


def _harness(monkey_recognizer: object) -> ApplicationRuntime:
    runtime = object.__new__(ApplicationRuntime)
    runtime._recognition_in_progress = False
    runtime._next_recognition_at = 0.0
    runtime.failed_attempts = 0
    runtime.status = "listening"
    runtime.capture = FakeCapture()
    runtime.recognizer = monkey_recognizer
    runtime.clients = set()
    runtime.recognition_window_seconds = 8.0
    runtime.recognition_timeout_seconds = 0.05
    runtime.public_state = lambda: {"state": runtime.status}  # noqa: E731
    runtime.broadcast = lambda payload=None: _completed()  # noqa: E731
    return runtime


async def _completed() -> None:
    return None


async def test_hung_provider_times_out_and_detection_continues() -> None:
    """A hung provider must back off, not wedge detection forever."""
    runtime = _harness(HangingRecognizer())

    await runtime.recognize_once()
    assert runtime.recognizer.calls == 1
    supervisor = runtime.supervisor
    assert supervisor.status == "idle"
    assert supervisor.failed_attempts == 1
    assert runtime._next_recognition_at > time.monotonic()

    # Detection must continue after the backoff instead of staying stuck.
    runtime._next_recognition_at = 0.0
    runtime.recognizer = EmptyRecognizer()
    await runtime.recognize_once()
    assert runtime.recognizer.calls == 1
    assert supervisor.failed_attempts == 2


async def test_unexpected_provider_error_recovers_instead_of_wedging() -> None:
    """An exotic provider exception must not leave status stuck at identifying."""

    class ExplodingRecognizer:
        async def recognize(self, wav_bytes: bytes) -> Track | None:
            del wav_bytes
            raise KeyError("unexpected payload shape")

    runtime = _harness(ExplodingRecognizer())
    await runtime.recognize_once()
    assert runtime.supervisor.status == "idle"
    assert runtime.supervisor.failed_attempts == 1


def test_track_change_survives_flaky_mid_play_miss() -> None:
    """Song B, miss, song B again must still switch (was: counter reset)."""
    player = PlayerState()
    player.set_track("Song A", "Artist A", duration=200.0, offset_seconds=0.0)
    supervisor = PlaybackSupervisor(player=player, clock=FakeClock(), status="playing")

    different = Track("Song B", "Artist B", offset_seconds=10.0)
    first = supervisor.handle_recognition_success(different, background=True)
    assert len(first) == 1 and isinstance(first[0], StartRecognition)

    # Flaky mid-play miss must not wipe the half-confirmed candidate.
    missed = supervisor.handle_recognition_no_track(background=True)
    assert missed == []
    assert player.current_track is not None and player.current_track.title == "Song A"

    second = supervisor.handle_recognition_success(different, background=True)
    assert len(second) == 2
    assert second[0].track.title == "Song B"


def test_stale_track_change_candidate_expires() -> None:
    """A 61s-old candidate must not combine with a fresh sighting."""
    clock = FakeClock()
    player = PlayerState()
    player.set_track("Song A", "Artist A", duration=200.0, offset_seconds=0.0)
    supervisor = PlaybackSupervisor(
        player=player, clock=clock, candidate_ttl_seconds=60.0, status="playing"
    )

    different = Track("Song B", "Artist B", offset_seconds=10.0)
    supervisor.handle_recognition_success(different, background=True)
    clock.advance(61.0)
    retry = supervisor.handle_recognition_success(different, background=True)
    assert len(retry) == 1 and isinstance(retry[0], StartRecognition)
    assert player.current_track is not None and player.current_track.title == "Song A"


def test_unknown_position_never_rewinds_clock() -> None:
    """A same-track re-check without position must leave the clock alone."""
    clock = FakeClock()
    player = PlayerState()
    player.set_track("Song A", "Artist A", duration=200.0, offset_seconds=0.0)
    assert player.current_track is not None
    player.current_track.started_at_monotonic = clock() - 100.0
    supervisor = PlaybackSupervisor(player=player, clock=clock, status="playing")

    actions = supervisor.handle_recognition_success(
        Track("Song A", "Artist A", offset_seconds=None), background=True
    )

    assert actions == []
    assert round(player.elapsed_seconds(clock())) == 100


def test_stale_identifying_status_reschedules_recognition() -> None:
    """Identifying + nothing in flight + backoff elapsed must not wedge."""
    clock = FakeClock()
    supervisor = PlaybackSupervisor(player=PlayerState(), clock=clock, status="identifying")
    supervisor.next_recognition_at = clock() - 1.0
    supervisor.signal_resumed_at = clock() - 10.0

    silence = SilenceState(active=True, became_active=False, became_silent=False, silent_for=0.0)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert supervisor.status == "listening"
    assert any(isinstance(a, StartRecognition) and not a.background for a in actions)


def test_stale_identifying_without_signal_recovers_silently() -> None:
    """Identifying wedge during silence resets to listening without scheduling."""
    clock = FakeClock()
    supervisor = PlaybackSupervisor(player=PlayerState(), clock=clock, status="identifying")
    supervisor.next_recognition_at = clock() - 1.0

    silence = SilenceState(active=False, became_active=False, became_silent=False, silent_for=5.0)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert supervisor.status == "listening"
    assert actions == []


async def test_rest_created_session_runs_control_loop() -> None:
    """A runtime first touched via REST must still capture and recognize."""
    manager = SessionManager(
        {
            "audio": {"source_id": "browser", "sample_rate": 44100},
            "server": {"host": "127.0.0.1", "port": 8000},
        }
    )
    try:
        runtime = manager.get_runtime("alice")
        assert runtime._control_task is not None and not runtime._control_task.done()
        assert runtime._spectrum_task is not None and not runtime._spectrum_task.done()

        same = await manager.get_or_create_runtime("alice")
        assert same is runtime
        assert not runtime._control_task.done()
    finally:
        await manager.stop_all()
