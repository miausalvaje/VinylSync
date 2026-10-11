# Architecture

## Overview

`VinylSync` is a local Python service with a static browser frontend. FastAPI serves the web UI, exposes HTTP endpoints, and streams status over WebSockets.

## Modules

- `audio`: discovers and manages input devices and optional Windows WASAPI loopback sources; it keeps a single continuous capture stream and a circular buffer of recent audio data.
- `audio.processing`: computes the FFT-based spectrum, RMS/peak metrics, windowed smoothing, and silence detection with hysteresis.
- `recognition`: defines the provider contract and normalizes track metadata before the app updates state.
- `lyrics`: queries LRCLIB, parses LRC text, and stores results in SQLite so the app can reuse them with a local cache.
- `artwork`: resolves and prioritizes cover art from the recognizer data, then falls back to iTunes Search / Cover Art Archive.
- `state`: maintains the active track, monotonic start time, drift tracking, and re-identification policy.
- `server`: exposes the REST API and WebSocket updates used by the browser.
- `web`: static HTML, CSS, and JavaScript for the fullscreen visualizer and lyrics experience.

## Data flow

1. The selected audio source streams mono, 44.1 kHz audio into a continuous circular buffer.
2. The buffer is used for spectrum visualization, silence detection, and 8-second recognition windows.
3. Detection logic only triggers a recognition call when there is sustained signal above the threshold for a configured minimum duration.
4. The recognizer returns normalized `Track` metadata and optional offset information.
5. `PlayerState` calculates the monotonic start time from the recognition offset and tracks drift during playback.
6. LRCLIB is queried through a local cache and fallback search path before lyrics are exposed to the UI.
7. State, track, source, idle, and error events are broadcast on `/ws`; the spectrum is streamed on `/ws/spectrum`.
8. The browser renders the visualizer, active track, and lyric timeline.

## Runtime states

- `listening`: the app is actively monitoring audio, but no track has been identified yet.
- `identifying`: a recognition request is in flight.
- `playing`: a recognized track is active and lyrics are being displayed.
- `idle`: the app has detected prolonged silence or repeated recognition failures and has returned to the visualizer state.

## HTTP API

- `GET /api/sources` and `POST /api/source` manage the active audio source.
- `GET /api/settings` and `PUT /api/settings` manage persistent settings.
- `GET /api/lyrics/search` searches LRCLIB for manual correction.
- `POST /api/track/override` locks a manually selected track.
- `POST /api/track/unlock` re-enables automatic recognition.

## Notes on source handling

- Input sources are exposed as IDs like `input:0`.
- Loopback sources are listed only on Windows when the platform supports them.
- Source switching is hot-pluggable: the app handles device disappearance, failed starts, and gracefully returns to the idle state.

## Current limitations

- ACRCloud and AudD network implementations remain secondary and are intentionally not treated as the primary path.
- Production polish for the browser UI is still being refined on top of the working architecture.
- Live turntable validation is still the final confirmation step for real-world recognition quality.
