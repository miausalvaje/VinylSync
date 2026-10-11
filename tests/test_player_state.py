"""Player state tests."""

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
