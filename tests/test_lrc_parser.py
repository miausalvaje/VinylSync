"""LRC parser tests."""

from vinyl_lyrics.lyrics.parser import parse_lrc


def test_parse_lrc_handles_multiple_timestamps_and_metadata():
    lyric = """
[ar:Artist]
[ti:Song]
[00:00.00]Hello
[00:02.50]World
[00:05.00]Again
"""

    parsed = parse_lrc(lyric)

    assert parsed[0] == (0.0, "Hello")
    assert parsed[1] == (2.5, "World")
    assert parsed[2] == (5.0, "Again")
    assert all(not item[1].startswith("[") for item in parsed)


def test_parse_lrc_handles_multiple_timestamps_on_same_line():
    """Test that multiple timestamps on one line are parsed correctly.

    This is the correct behavior for LRC files where a line like:
    [00:30.00]First chorus[01:30.00]Second chorus
    should produce two entries with different text for each timestamp.
    """
    lyric = "[00:30.00]First chorus[01:30.00]Second chorus"
    parsed = parse_lrc(lyric)

    assert len(parsed) == 2
    assert parsed[0] == (30.0, "First chorus")
    assert parsed[1] == (90.0, "Second chorus")


def test_parse_lrc_preserves_blank_instrumental_gap():
    lyric = "[00:00.00]Intro\n[00:03.00]\n[00:05.00]Verse"
    parsed = parse_lrc(lyric)
    assert (0.0, "Intro") in parsed
    assert (3.0, "") in parsed
    assert (5.0, "Verse") in parsed
