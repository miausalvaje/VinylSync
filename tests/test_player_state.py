"""Player state tests."""

import pytest

from vinyl_lyrics.state.player import PlayerState


def test_player_state_tracks_elapsed_time_and_offset():
    player = PlayerState(reidentify_interval=45.0)
    track = player.set_track("Song", "Artist", offset_seconds=12.5)

    assert player.current_track is track
    assert track.offset_seconds == 12.5
    assert track.elapsed_seconds() >= 0.0


def test_player_state_holds_a_jump_until_a_fresh_sample_confirms_it():
    # Real case: an edited pressing of "Sad Songs" ran ~17.5 s ahead of the
    # reference from the cut on.
    player = PlayerState(reidentify_interval=45.0)
    track = player.set_track("Sad Songs", "Elton John", offset_seconds=10.0)
    started = track.started_at_monotonic

    held = player.adjust_offset_if_needed(72.5, now=started + 55.0)

    assert held == 10.0
    assert track.started_at_monotonic == started
    assert player.needs_position_confirmation

    confirmed = player.adjust_offset_if_needed(78.6, now=started + 61.0)

    assert confirmed == 78.6
    assert track.position(started + 61.0) == pytest.approx(78.6)
    assert not player.needs_position_confirmation


def test_player_state_drops_a_lone_outlier():
    # Real re-checks of "I'm Still Standing" (12 s samples, so all 1 s high):
    # the middle one landed ~2 s early and the next agreed with the first.
    player = PlayerState(reidentify_interval=45.0)
    track = player.set_track("I'm Still Standing", "Elton John", offset_seconds=52.922)
    detected_at = track.started_at_monotonic + 52.922

    player.adjust_offset_if_needed(96.299, now=detected_at + 45.360)

    assert track.position(detected_at + 45.360) == pytest.approx(52.922 + 45.360)
    assert player.needs_position_confirmation

    player.adjust_offset_if_needed(143.558, now=detected_at + 90.800)

    assert not player.needs_position_confirmation
    assert track.position(detected_at + 90.800) == pytest.approx(143.722, abs=0.15)


def test_player_state_snaps_small_errors_and_halves_larger_ones():
    player = PlayerState(reidentify_interval=45.0)
    track = player.set_track("Song", "Artist", offset_seconds=10.0, playback_rate=1.0)
    detected_at = track.started_at_monotonic + 10.0

    snapped = player.adjust_offset_if_needed(55.1, now=detected_at + 45.0, playback_rate=1.0)
    assert snapped == pytest.approx(55.1)
    # 0.4 s off is within tolerance but large for a single sample: move halfway.
    halved = player.adjust_offset_if_needed(100.5, now=detected_at + 90.0, playback_rate=1.0)
    assert halved == pytest.approx(100.3)


def test_player_state_ignores_tiny_offset_jitter():
    player = PlayerState(reidentify_interval=45.0)
    track = player.set_track("Song", "Artist", offset_seconds=10.0, playback_rate=1.0)
    started = track.started_at_monotonic

    adjusted = player.adjust_offset_if_needed(55.03, now=started + 55.0, playback_rate=1.0)

    assert adjusted == 10.0
    assert track.started_at_monotonic == started


def test_clock_runs_at_the_reported_playback_rate():
    player = PlayerState()
    track = player.set_track("Song", "Artist", offset_seconds=20.0, playback_rate=1.005)

    assert track.position(track.started_at_monotonic + 100.0) == pytest.approx(100.5)
    assert player.elapsed_seconds(track.started_at_monotonic + 100.0) == pytest.approx(100.5)


def test_implausible_rate_is_ignored():
    player = PlayerState()
    track = player.set_track("Song", "Artist", offset_seconds=20.0, playback_rate=1.35)

    assert track.playback_rate == 1.0


def test_rate_is_estimated_from_positions_when_not_reported():
    # Real drift of an Elton John pressing: ~0.07 s behind per 45 s re-check.
    player = PlayerState()
    track = player.set_track("Daniel", "Elton John", offset_seconds=30.0)
    detected_at = track.started_at_monotonic + 30.0

    player.adjust_offset_if_needed(30.0 + 45.4 - 0.065, now=detected_at + 45.4)

    assert track.playback_rate == pytest.approx(1 - 0.065 / 45.4)
    later = detected_at + 90.8
    assert track.position(later) == pytest.approx(30.0 + 90.8 - 0.13, abs=0.01)


def test_reported_rates_are_combined_with_the_median():
    player = PlayerState()
    track = player.set_track("Song", "Artist", offset_seconds=0.0, playback_rate=0.998)
    detected_at = track.started_at_monotonic

    player.adjust_offset_if_needed(44.91, now=detected_at + 45.0, playback_rate=0.9981)
    player.adjust_offset_if_needed(89.82, now=detected_at + 90.0, playback_rate=1.04)

    assert track.playback_rate == pytest.approx(0.9981)


def test_manual_offset_changes_lyric_clock_not_playback_clock():
    player = PlayerState()
    track = player.set_track("Song", "Artist", offset_seconds=10.0, manual_offset_seconds=0.5)

    assert track.elapsed_seconds(track.started_at_monotonic) == 0.0
    assert track.lyric_elapsed_seconds(track.started_at_monotonic) == 0.5


def test_manual_offset_does_not_change_track_end_calculations():
    player = PlayerState()
    track = player.set_track("Song", "Artist", duration=60.0, manual_offset_seconds=20.0)

    assert player.remaining_seconds(track.started_at_monotonic + 10.0) == pytest.approx(50.0)
    assert not player.is_past_end(now=track.started_at_monotonic + 45.0)
