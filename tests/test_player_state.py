"""Player state tests."""

import pytest

from vinyl_lyrics.state.player import PlayerState


def test_player_state_tracks_elapsed_time_and_offset():
    player = PlayerState(reidentify_interval=45.0)
    track = player.set_track("Song", "Artist", offset_seconds=12.5)

    assert player.current_track is track
    assert track.offset_seconds == 12.5
    assert track.elapsed_seconds() >= 0.0


def test_player_state_rebases_when_offset_moves_too_far():
    player = PlayerState(reidentify_interval=45.0)
    player.set_track("Song", "Artist", offset_seconds=10.0)

    adjusted = player.adjust_offset_if_needed(13.0, now=100.0)

    assert adjusted == 13.0
    assert player.current_track is not None
    assert player.current_track.offset_seconds == 13.0
    assert player.current_track.started_at_monotonic == 100.0 - 13.0


def test_player_state_blends_in_small_persistent_offset_errors():
    # Real case: the first match after a silence was ~1 s low and every later
    # re-check agreed on ~0.97 s more, below the old 1.5 s rebase threshold.
    player = PlayerState(reidentify_interval=45.0)
    track = player.set_track("Do Ya", "ELO", offset_seconds=7.72)
    started = track.started_at_monotonic

    adjusted = player.adjust_offset_if_needed(54.08, now=started + 53.11)

    assert adjusted == pytest.approx(53.595)
    assert track.started_at_monotonic == pytest.approx(started - 0.485)

    second = player.adjust_offset_if_needed(99.51, now=started + 98.55)

    assert second == pytest.approx(98.55 + 0.485 + (99.51 - 98.55 - 0.485) / 2)


def test_player_state_ignores_tiny_offset_jitter():
    player = PlayerState(reidentify_interval=45.0)
    track = player.set_track("Song", "Artist", offset_seconds=10.0)
    started = track.started_at_monotonic

    adjusted = player.adjust_offset_if_needed(45.2, now=started + 45.0)

    assert adjusted == 10.0
    assert track.started_at_monotonic == started


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
