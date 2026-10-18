# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Initial open-source project structure.
- Audio capture, recognition contracts, LRCLIB lookup, LRC parsing, caching,
  player state, and local FastAPI/WebSocket backend foundations.
- `BrowserStreamSource` audio source implementation enabling headless and server deployments (e.g. Proxmox LXC) without local sound hardware.
- WebSocket streaming endpoint `/ws/audio` with binary PCM Int16 support, JSON handshake, single-capturer takeover mechanism, 64 KB frame limits, and token bucket rate limiting.
- Stream clock synchronization anchored to first arrival, with NumPy-based resampling, micro-drift correction, and automatic zero-filling for packet gaps.
- Client ID generation and `capturer` state event broadcasts.
- Frontend browser audio capture with `AudioWorklet` processor (no timers), Screen Wake Lock, Web Lock, and device selector (`getUserMedia` and `getDisplayMedia`).
- Reverse proxy support with portless origin parsing, explicit `server.allowed_origins`, and spoofing-resistant `server.trusted_proxies` IP/CIDR filtering.
- Optional token-based authentication (`VINYLSYNC_AUTH_TOKEN`) with constant-time comparison, rate-limited failed attempts, and browser unlock modal dialog.
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
- A silence inside a gap of the synced lyrics (an ambient interlude, like the minute of near silence in twenty one pilots' "Paladin Strait") is no longer taken for a needle lift: `break_max_seconds` counts from the next sung line.

### TODO

- Complete the polished frontend.
- Complete ACRCloud and AudD provider integrations.
