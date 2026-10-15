"""Tests for PlaybackSupervisor decision logic and silence handling."""

import pytest

from vinyl_lyrics.audio.processing import SilenceState
from vinyl_lyrics.config import validate_config
from vinyl_lyrics.recognition.base import Track
from vinyl_lyrics.state.player import PlayerState
from vinyl_lyrics.state.supervisor import (
    ApplyTrack,
    BroadcastEvent,
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


def test_break_in_middle_of_song() -> None:
    """1. Break in middle of song preserves track, advances time, and verifies on return."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    # Track duration 200s, started 90s ago
    player.set_track("Song A", "Artist A", duration=200.0, offset_seconds=0.0)
    assert player.current_track is not None
    player.current_track.started_at_monotonic = clock() - 90.0

    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        break_max_seconds=10.0,
        end_margin_seconds=10.0,
        status="playing",
    )

    # Signal drops: became_silent
    silence = SilenceState(active=False, became_active=False, became_silent=True, silent_for=1.5)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert actions == []
    assert supervisor.silence_type == "break"
    assert supervisor.status == "playing"
    assert player.current_track is not None

    # Silence continues for 4 seconds
    clock.advance(4.0)
    silence = SilenceState(active=False, became_active=False, became_silent=False, silent_for=4.0)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert actions == []
    assert supervisor.status == "playing"
    assert player.current_track is not None
    # Elapsed time continued running during silence (90 + 4 = 94s)
    assert round(player.elapsed_seconds(clock())) == 94.0

    # Signal returns: became_active
    silence = SilenceState(active=True, became_active=True, became_silent=False, silent_for=0.0)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert len(actions) == 1
    assert isinstance(actions[0], StartRecognition)
    assert actions[0].background is True
    assert actions[0].cancel_current is True
    assert supervisor.status == "playing"

    # Same song verified with small drift (<= 1.5s): no offset adjustment event
    same_track = Track("Song A", "Artist A", offset_seconds=94.5)
    result_actions = supervisor.handle_recognition_success(same_track, background=True)
    assert result_actions == []
    assert supervisor.status == "playing"

    # Same song verified with large drift (> 1.5s): offset adjusted and state emitted
    drifted_track = Track("Song A", "Artist A", offset_seconds=98.0)
    result_actions = supervisor.handle_recognition_success(drifted_track, background=True)
    assert len(result_actions) == 1
    assert isinstance(result_actions[0], BroadcastEvent)
    assert result_actions[0].event == "state"


def test_silence_near_end_is_boundary() -> None:
    """2. Silence near end (5s remaining <= end_margin): boundary, discards on return."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    # Track duration 100s, 95s elapsed (5s remaining)
    player.set_track("Song A", "Artist A", duration=100.0, offset_seconds=0.0)
    assert player.current_track is not None
    player.current_track.started_at_monotonic = clock() - 95.0

    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        end_margin_seconds=10.0,
        status="playing",
    )
    # Simulate recent periodic recognition timer far in the future
    supervisor.next_recognition_at = clock() + 40.0

    # became_silent near end
    silence = SilenceState(active=False, became_active=False, became_silent=True, silent_for=1.5)
    supervisor.handle_silence(silence, recognition_busy=False)
    assert supervisor.silence_type == "boundary"
    assert supervisor.status == "playing"

    # Signal returns after track boundary: track discarded,
    # status becomes listening, and next_recognition_at is reset
    silence = SilenceState(active=True, became_active=True, became_silent=False, silent_for=0.0)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert len(actions) == 1
    assert isinstance(actions[0], BroadcastEvent)
    assert actions[0].event == "idle"
    assert player.current_track is None
    assert supervisor.status == "listening"
    assert supervisor.next_recognition_at == 0.0
    assert supervisor.failed_attempts == 0


def test_silence_exceeding_break_max_seconds_is_needle_lift() -> None:
    """3. Silence exceeding break_max_seconds in middle of song discards and goes idle."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    player.set_track("Song A", "Artist A", duration=200.0, offset_seconds=0.0)
    assert player.current_track is not None
    player.current_track.started_at_monotonic = clock() - 50.0

    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        break_max_seconds=10.0,
        status="playing",
    )

    # became_silent
    silence = SilenceState(active=False, became_active=False, became_silent=True, silent_for=1.5)
    supervisor.handle_silence(silence, recognition_busy=False)
    assert supervisor.silence_type == "break"

    # Silence reaches 10.5 seconds (> break_max_seconds)
    clock.advance(9.0)
    silence = SilenceState(active=False, became_active=False, became_silent=False, silent_for=10.5)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert len(actions) == 1
    assert isinstance(actions[0], BroadcastEvent)
    assert actions[0].event == "idle"
    assert supervisor.status == "idle"
    assert player.current_track is None


def test_unknown_duration_treated_as_break() -> None:
    """4. Unknown duration is treated as break until break_max_seconds."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    player.set_track("Song A", "Artist A", duration=None, offset_seconds=0.0)

    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        break_max_seconds=10.0,
        status="playing",
    )

    silence = SilenceState(active=False, became_active=False, became_silent=True, silent_for=1.5)
    supervisor.handle_silence(silence, recognition_busy=False)
    assert supervisor.silence_type == "break"
    assert supervisor.status == "playing"
    assert player.current_track is not None


def test_verification_debounces_different_song() -> None:
    """5. Verification returning another song requires 2 consecutive samples before switching."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    player.set_track("Song A", "Artist A", duration=200.0, offset_seconds=0.0)

    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        status="playing",
    )

    different_track = Track("Song B", "Artist B", offset_seconds=10.0)

    # First detection of different track: requests confirmation sample, does NOT switch
    actions1 = supervisor.handle_recognition_success(different_track, background=True)
    assert len(actions1) == 1
    assert isinstance(actions1[0], StartRecognition)
    assert actions1[0].background is True
    assert player.current_track is not None and player.current_track.title == "Song A"
    assert supervisor.status == "playing"

    # Second matching detection of different track: confirms switch!
    actions2 = supervisor.handle_recognition_success(different_track, background=True)
    assert len(actions2) == 2
    assert isinstance(actions2[0], ApplyTrack)
    assert actions2[0].track.title == "Song B"
    assert isinstance(actions2[1], BroadcastEvent)
    assert actions2[1].event == "track"


def test_verification_no_result_applies_backoff_and_stays_playing() -> None:
    """6. Verification with no result keeps current song and applies backoff."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    player.set_track("Song A", "Artist A", duration=200.0, offset_seconds=0.0)

    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        status="playing",
    )

    # No track result
    actions = supervisor.handle_recognition_no_track(background=True)
    assert actions == []
    assert supervisor.status == "playing"
    assert player.current_track is not None
    assert supervisor.next_recognition_at == clock() + 8.0

    # Subsequent failure
    supervisor.handle_recognition_no_track(background=True)
    assert supervisor.next_recognition_at == clock() + 16.0
    assert supervisor.status == "playing"


def test_periodic_reidentification_failure_stays_playing() -> None:
    """7. Periodic re-identification error with active track keeps playing without events."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    player.set_track("Song A", "Artist A", duration=200.0, offset_seconds=0.0)

    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        status="playing",
    )

    actions = supervisor.handle_recognition_error(RuntimeError("network error"), background=True)
    assert actions == []
    assert supervisor.status == "playing"
    assert player.current_track is not None
    assert supervisor.next_recognition_at == clock() + 8.0


def test_locked_track_survives_break_and_unlocks_on_boundary() -> None:
    """8. Locked track survives break, unlocks on boundary and needle lift."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    player.set_track("Song A", "Artist A", duration=200.0, offset_seconds=0.0)
    assert player.current_track is not None
    player.current_track.started_at_monotonic = clock() - 50.0

    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        break_max_seconds=10.0,
        end_margin_seconds=10.0,
        status="playing",
    )
    supervisor.locked = True

    # Break in middle of song
    silence = SilenceState(active=False, became_active=False, became_silent=True, silent_for=1.5)
    supervisor.handle_silence(silence, recognition_busy=False)
    assert supervisor.silence_type == "break"
    assert supervisor.locked is True

    # Signal returns: locked track preserved
    silence = SilenceState(active=True, became_active=True, became_silent=False, silent_for=0.0)
    supervisor.handle_silence(silence, recognition_busy=False)
    assert supervisor.locked is True
    assert player.current_track is not None

    # Fast-forward to boundary (duration 200s, elapsed 195s)
    player.current_track.started_at_monotonic = clock() - 195.0
    silence = SilenceState(active=False, became_active=False, became_silent=True, silent_for=1.5)
    supervisor.handle_silence(silence, recognition_busy=False)
    assert supervisor.silence_type == "boundary"

    # Signal returns after boundary: song discarded and locked cleared
    silence = SilenceState(active=True, became_active=True, became_silent=False, silent_for=0.0)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert len(actions) == 1
    assert isinstance(actions[0], BroadcastEvent)
    assert actions[0].event == "idle"
    assert supervisor.locked is False
    assert player.current_track is None
    assert supervisor.status == "listening"


def test_first_identification_waits_for_min_sample_seconds() -> None:
    """9. First identification after startup / idle waits for min_sample_seconds clean audio."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        min_sample_seconds=5.0,
        status="idle",
    )

    # First audio detected: became_active
    silence = SilenceState(active=True, became_active=True, became_silent=False, silent_for=0.0)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert supervisor.status == "listening"
    assert supervisor.signal_resumed_at == 1000.0
    assert actions == []

    # Audio active for only 2 seconds (< 5s min_sample_seconds): no recognition yet
    clock.advance(2.0)
    silence = SilenceState(active=True, became_active=False, became_silent=False, silent_for=0.0)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert actions == []

    # Audio active for 5.1 seconds (>= 5s min_sample_seconds): triggers recognition
    clock.advance(3.1)
    actions = supervisor.handle_silence(silence, recognition_busy=False)
    assert len(actions) == 1
    assert isinstance(actions[0], StartRecognition)
    assert actions[0].since == 1000.0
    assert actions[0].min_sample_seconds == 5.0
    assert actions[0].background is False


def test_three_consecutive_failures_without_song_transitions_to_idle() -> None:
    """10. Without song: 3 consecutive failures transition to idle with backoff."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        status="listening",
    )

    # 1st failure: delay 8s, remains listening
    actions = supervisor.handle_recognition_no_track(background=False)
    assert supervisor.status == "listening"
    assert supervisor.next_recognition_at == clock() + 8.0
    assert len(actions) == 1
    assert isinstance(actions[0], BroadcastEvent)
    assert actions[0].event == "listening"

    # 2nd failure: delay 16s, remains listening
    actions = supervisor.handle_recognition_no_track(background=False)
    assert supervisor.status == "listening"
    assert supervisor.next_recognition_at == clock() + 16.0

    # 3rd failure: delay 30s, transitions to idle
    actions = supervisor.handle_recognition_no_track(background=False)
    assert supervisor.status == "idle"
    assert supervisor.next_recognition_at == clock() + 30.0
    assert len(actions) == 1
    assert isinstance(actions[0], BroadcastEvent)
    assert actions[0].event == "idle"


def test_gapless_signal_without_edge_still_triggers_recognition() -> None:
    """Continuous audio with no became_active edge must still identify.

    Regression: signal_resumed_at is only set on became_active transitions
    and cleared on every success, so a gapless track change (detector never
    cycles) left resumed=None and the scheduler never fired.
    """
    clock = FakeClock(1000.0)
    player = PlayerState()
    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        min_sample_seconds=5.0,
        status="listening",
    )
    assert supervisor.signal_resumed_at is None

    steady = SilenceState(active=True, became_active=False, became_silent=False, silent_for=0.0)
    actions = supervisor.handle_silence(steady, recognition_busy=False)
    assert actions == []

    clock.advance(5.1)
    actions = supervisor.handle_silence(steady, recognition_busy=False)
    assert len(actions) == 1
    assert isinstance(actions[0], StartRecognition)
    assert actions[0].background is False


def test_signal_pause_resets_sustained_tracking() -> None:
    """A silence gap restarts the sustained-signal measurement."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        min_sample_seconds=5.0,
        status="listening",
    )
    steady = SilenceState(active=True, became_active=False, became_silent=False, silent_for=0.0)
    clock.advance(3.0)
    supervisor.handle_silence(steady, recognition_busy=False)

    quiet = SilenceState(active=False, became_active=False, became_silent=False, silent_for=3.0)
    supervisor.handle_silence(quiet, recognition_busy=False)
    assert supervisor.signal_active_since is None

    clock.advance(3.0)
    actions = supervisor.handle_silence(steady, recognition_busy=False)
    assert actions == []


def test_no_overlapping_recognition_and_break_cancels_periodic() -> None:
    """11. No overlapping: busy check prevents start; break verification cancels periodic."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    player.set_track("Song A", "Artist A", duration=200.0, offset_seconds=0.0)

    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        status="playing",
    )

    # Signal active and periodic check due, but recognition is currently busy: does not start
    supervisor.player.reset_recheck_timer(clock() - 50.0)
    supervisor.next_recognition_at = clock() - 1.0
    silence = SilenceState(active=True, became_active=False, became_silent=False, silent_for=0.0)
    actions = supervisor.handle_silence(silence, recognition_busy=True)
    assert actions == []

    # Break return triggers recognition with cancel_current=True
    supervisor.silence_type = "break"
    silence = SilenceState(active=True, became_active=True, became_silent=False, silent_for=0.0)
    actions = supervisor.handle_silence(silence, recognition_busy=True)
    assert len(actions) == 1
    assert isinstance(actions[0], StartRecognition)
    assert actions[0].cancel_current is True


def test_locked_track_not_replaced_by_verification() -> None:
    """12. A locked track is never replaced by background verification."""
    clock = FakeClock(1000.0)
    player = PlayerState()
    player.set_track("Locked Song", "Locked Artist", duration=200.0, offset_seconds=10.0)

    supervisor = PlaybackSupervisor(
        player=player,
        clock=clock,
        status="playing",
    )
    supervisor.locked = True

    different_track = Track("Other Song", "Other Artist", offset_seconds=50.0)

    # Verification returns a completely different track
    actions1 = supervisor.handle_recognition_success(different_track, background=True)
    assert actions1 == []
    assert player.current_track is not None
    assert player.current_track.title == "Locked Song"
    assert supervisor.locked is True

    # Repeated detection still does not replace locked song
    actions2 = supervisor.handle_recognition_success(different_track, background=True)
    assert actions2 == []
    assert player.current_track is not None
    assert player.current_track.title == "Locked Song"
    assert supervisor.status == "playing"


def test_config_validation_rules() -> None:
    """Validate positive values and break_max_seconds >= track_gap_seconds."""
    valid = {
        "audio": {"sample_rate": 44100, "buffer_seconds": 15},
        "recognition": {
            "track_gap_seconds": 1.5,
            "break_max_seconds": 10.0,
            "end_margin_seconds": 10.0,
            "interval_seconds": 45,
            "min_signal_seconds": 1.0,
            "idle_timeout_seconds": 20,
            "min_sample_seconds": 5,
        },
    }
    assert validate_config(valid) == valid

    # break_max_seconds < track_gap_seconds must fail
    invalid_break = {"recognition": {"track_gap_seconds": 5.0, "break_max_seconds": 2.0}}
    with pytest.raises(ValueError, match="break_max_seconds"):
        validate_config(invalid_break)

    # negative value must fail
    invalid_negative = {"recognition": {"interval_seconds": -1}}
    with pytest.raises(ValueError, match="positive"):
        validate_config(invalid_negative)
