# AGENTS.md - VinylSync Development Guide for AI Agents

## Project Overview

**VinylSync** is a local Python service with a static browser frontend that provides synchronized lyrics display for turntable audio. It captures audio from local hardware (soundcards, USB interfaces, WASAPI loopback) or remote browser-based audio capture over WebSockets, performs music recognition (Shazam, ACRCloud, AudD), fetches lyrics from LRCLIB, and displays them in a real-time web UI.

**Tech Stack**: Python 3.11+, FastAPI, uvicorn, httpx, NumPy, sounddevice, shazamio, SQLite

## Architecture Summary

```
Audio Ingestion → ContinuousAudioCapture → SilenceDetector + SpectrumAnalyzer
                                                  ↓
                                          PlaybackSupervisor
                                                  ↓
                                          Recognition Provider (Shazam/ACRCloud/AudD)
                                                  ↓
                                           PlayerState + Lyrics Cache
                                                  ↓
                                          WebSocket Broadcast → Frontend
```

### Key Modules (`src/vinyl_lyrics/`)

| Module | Purpose |
|--------|---------|
| `audio/capture.py` | Continuous audio capture with circular buffer, source switching |
| `audio/processing.py` | FFT spectrum, RMS/peak metrics, silence detection with hysteresis |
| `audio/source.py` | Audio source abstraction (local devices, browser stream) |
| `audio/devices.py` | Device enumeration and resolution |
| `recognition/base.py` | Provider interface and `Track` dataclass |
| `recognition/shazam.py` | ShazamIO integration |
| `recognition/acrcloud.py` | ACRCloud provider (stub) |
| `recognition/audd.py` | AudD provider (stub) |
| `lyrics/cache.py` | SQLite lyrics cache |
| `lyrics/lrclib.py` | LRCLIB API client |
| `lyrics/parser.py` | LRC format parsing |
| `lyrics/multi.py` | Multi-provider lyrics fallback |
| `artwork/resolver.py` | Cover art resolution (iTunes, Cover Art Archive) |
| `state/player.py` | Track state, elapsed time, re-identification logic |
| `state/supervisor.py` | `PlaybackSupervisor` - state machine for playback transitions |
| `settings.py` | User settings persistence (JSON) |
| `config.py` | YAML configuration loading |
| `server/api.py` | REST endpoints (health, auth, settings, sources, state, lyrics, track override) |
| `server/ws.py` | WebSocket handlers (state, spectrum, audio streaming) |
| `server/security.py` | Host/origin validation, proxy trust, token auth, rate limiting |
| `server/session.py` | Per-user session management |
| `server/users.py` | User management, invitations, sessions |
| `main.py` | Application runtime, FastAPI factory, control loops |

### Frontend (`web/`)

Static HTML/CSS/JS served by FastAPI. Key JS modules:
- `app.js` - Main application, WebSocket connections, state management
- `visualizer.js` - Canvas-based audio visualization
- `audio-capture.js` / `audio-worklet.js` - Browser audio capture via AudioWorklet
- `lyrics-sync.js` - Synchronized lyrics display
- `settings.js` - Settings panel logic
- `manual-search.js` - Track correction search dialog
- `fullscreen.js` - Fullscreen handling
- `background.js` - Artwork backdrop effects

## Runtime States

| State | Description |
|-------|-------------|
| `no_source` | Waiting for audio source (browser mode, no streamer connected) |
| `listening` | Monitoring audio, no track identified |
| `identifying` | Recognition request in flight |
| `playing` | Track recognized, lyrics displayed, clock running |
| `idle` | Prolonged silence / tonearm lift / failed recognition |

## Configuration

**Primary**: `config.yaml` (committed, version-controlled)
- `audio`: source_id, sample_rate, buffer_seconds, gain_db, silence_threshold_db
- `recognition`: provider, interval_seconds, min_signal_seconds, track_gap_seconds, break_max_seconds, end_margin_seconds, idle_timeout_seconds, min_sample_seconds
- `lyrics`: lrclib_base_url, user_agent, cache_path
- `server`: host, port, allowed_hosts, trust_proxy, trusted_proxies, allowed_origins
- `ui`: font_size, manual_offset_seconds

**Runtime overrides**: `settings.json` (per-user, persisted, not committed)

**Secrets**: `VINYLSYNC_AUTH_TOKEN` environment variable (never in config)

## Development Workflow

### Setup
```powershell
python -m pip install -e ".[dev]"
pre-commit install
```

### Quality Checks (run before committing)
```powershell
pytest
ruff check .
ruff format --check .
mypy
```

### Formatting
```powershell
ruff format .
```

### Run Server
```powershell
vinylsync
# or
python -m vinyl_lyrics.main
```

## Key Design Patterns

### 1. Supervisor Action Pattern
`PlaybackSupervisor` returns `SupervisorAction` dataclasses (`StartRecognition`, `BroadcastEvent`, `ApplyTrack`) which `ApplicationRuntime._execute_actions()` processes. This decouples decision logic from execution.

### 2. Single Capturer Semantics (Browser Mode)
Only one browser can stream audio at a time via `/ws/audio`. Others receive `busy` response and can request `takeover`.

### 3. Per-User Sessions
`SessionManager` creates isolated `ApplicationRuntime` instances per authenticated user (keyed by username). Unauthenticated requests use "default" runtime.

### 4. Recognition Scheduling
- Backoff delays: 8s, 16s, 30s after failures
- Periodic re-identification every `interval_seconds` (default 45s) when `playing` and not `locked`
- Minimum sample duration (`min_sample_seconds`, default 5s) before recognition
- Two-sample debouncing for track changes during playback

### 5. Silence Classification
- `break`: Mid-song silence (remaining > `end_margin_seconds`)
- `boundary`: End-of-track silence (remaining ≤ `end_margin_seconds`)
- `idle`: Silence ≥ `break_max_seconds` (needle lift) or ≥ `idle_timeout_seconds` after boundary

## Testing

Tests in `tests/` use pytest with `pythonpath = ["src"]`. Key test files:
- `test_audio_processing.py` - Spectrum, silence detection
- `test_recognition_*.py` - Provider contracts, gate logic
- `test_supervisor.py` - State machine transitions
- `test_player_state.py` - Track timing, re-identification
- `test_lyrics_cache.py` / `test_lrclib.py` - Lyrics fetching and caching
- `test_browser_stream.py` - WebSocket audio streaming
- `test_server_security.py` - Auth, proxy, origin validation

Run: `pytest` or `pytest tests/test_specific.py -v`

## Common Tasks

### Add a New Recognition Provider
1. Implement `RecognitionProvider` in `recognition/<name>.py`
2. Register in `recognition/__init__.py` `create_recognizer()`
3. Add config section in `config.yaml`
4. Add tests in `tests/test_<name>.py`

### Add a New Lyrics Source
1. Implement `LyricsProvider` protocol in `lyrics/<name>.py`
2. Integrate in `lyrics/multi.py` `MultiProviderLyricsClient`
3. Add config if needed

### Modify Silence/Playback Logic
Edit `state/supervisor.py` `PlaybackSupervisor.handle_silence()` and related handlers. Update tests in `test_supervisor.py`.

### Change Frontend Behavior
Edit files in `web/js/` and `web/css/`. No build step - served directly.

### Add API Endpoint
1. Add route in `server/api.py` or new router
2. Use `_get_request_runtime(request)` for per-user runtime
3. Apply auth via `_get_current_user(request)` where needed

## Security Considerations

- **Never log or commit** `VINYLSYNC_AUTH_TOKEN`, user passwords, session tokens
- `server.trust_proxy` only effective when peer IP in `server.trusted_proxies`
- Token auth: `Authorization: Bearer <token>` (REST) or `vinylsync-auth.<token>` subprotocol / auth message (WS)
- Rate limiting on failed auth attempts per IP
- Input validation via Pydantic models (`server/models.py`)

## Performance Notes

- Spectrum loop runs at ~40 FPS only when clients connected (`spectrum_clients` set)
- Control loop sleeps 100ms when idle, processes 250ms audio windows
- Recognition uses 12-second windows from circular buffer by default (configurable)
- Browser audio resampled in-memory via NumPy to 44.1 kHz
- Lyrics cached in SQLite (`data/lyrics.sqlite3`) keyed by artist/title/duration

## Debugging Tips

- Enable debug logging: `logging.basicConfig(level=logging.DEBUG)` in `main.py`
- Check `/api/health` for auth requirement status
- WebSocket state events: `state`, `track`, `idle`, `identifying`, `capturer`, `source_changed`
- Spectrum WebSocket: `/ws/spectrum` (bands, rms, peak, dbfs)
- Audio WebSocket: `/ws/audio` (binary PCM Int16 frames, JSON handshake)

## File Conventions

- Python: Type hints required, `slots=True` dataclasses, Google-style docstrings
- Ruff: line-length 100, double quotes, target py311
- MyPy: strict mode, disallow untyped defs
- Tests: async with `pytest-asyncio`, fixtures in `conftest.py`
- Commits: Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, etc.)

## Known Limitations / TODO (from CHANGELOG)

- Complete polished frontend
- Complete ACRCloud and AudD provider integrations
- Frontend visualizer and lyrics sync need refinement