# Architecture

## Overview

`vinyl-lyrics` is a local Python service with a static browser frontend.
FastAPI serves the frontend and exposes HTTP and WebSocket endpoints.

## Modules

- `audio`: discovers input and Windows WASAPI loopback sources, keeps one source
  continuously open, and stores approximately 15 seconds in a circular buffer.
- `audio.processing`: computes smoothed 64-band FFT data, RMS/peak levels, and
  hysteresis-based silence state.
- `recognition`: defines the provider contract and normalizes track metadata.
- `lyrics`: queries LRCLIB, parses synchronized LRC text, and stores results
  in SQLite.
- `artwork`: reserved for artwork lookup and resolution.
- `state`: tracks the active song, monotonic start time, and offset drift.
- `server`: exposes the REST API and WebSocket updates.
- `web`: static HTML, CSS, and JavaScript presentation.

## Data flow

1. A selected `AudioSource` continuously provides mono frames.
2. The circular buffer supplies visualization metrics and approximately
   eight-second recognition windows.
3. The selected recognizer returns normalized `Track` metadata only after
   sustained signal is detected.
4. `PlayerState` calculates the monotonic start time from the recognition
   offset.
5. `LyricsCache` is checked before LRCLIB is queried.
6. State is broadcast over `/ws`; spectrum data uses `/ws/spectrum`.
7. The browser renders the current track and lyrics.

## Runtime states

- `listening`: signal may be present, but no track is identified.
- `identifying`: a recognition request is in progress.
- `playing`: a track and its lyrics are active.
- `idle`: silence is prolonged or recognition repeatedly fails.

## HTTP API

- `GET /api/sources` and `POST /api/source` manage the active source.
- `GET /api/settings` and `PUT /api/settings` manage persistent settings.
- `GET /api/lyrics/search` searches LRCLIB for manual correction.
- `POST /api/track/override` locks a manually selected track.
- `POST /api/track/unlock` enables automatic recognition again.

## Current limitations

- TODO: finish the production frontend and lyric synchronization controls.
- TODO: implement ACRCloud and AudD network requests.
- TODO: wire artwork fallback providers.
