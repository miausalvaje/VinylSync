# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Initial open-source project structure.
- Audio capture, recognition contracts, LRCLIB lookup, LRC parsing, caching,
  player state, and local FastAPI/WebSocket backend foundations.
- `PlaybackSupervisor` orchestration extracting playback policy and state transitions with an injectable clock.
- Silence classification: distinguishes between mid-song breaks, song transitions (`boundary`), and needle lifts (`idle`).
- Track metrics in `TrackState` and `PlayerState`: `remaining_seconds()` and `is_past_end()`.
- Non-intrusive background verification on signal resume after mid-song breaks without flickering to `identifying`.
- Two-sample debouncing before switching songs during playback verification and periodic re-identification.
- Configuration parameters `recognition.break_max_seconds` and `recognition.end_margin_seconds`, with strict validation.

### Changed

- Mid-song instrumental breaks or quiet passages no longer clear active lyrics or cause re-recognition from scratch; the playback clock advances during silence.
- Post-boundary and startup recognitions bypass backoff timers and wait for clean audio instead of previous silence.
- Periodic re-identification failures with an active track no longer drop the player to `listening` or `idle`.

### TODO

- Complete the polished frontend.
- Complete ACRCloud and AudD provider integrations.
