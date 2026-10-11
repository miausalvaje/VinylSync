"""Application entry point and continuous runtime orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vinyl_lyrics.audio.capture import ContinuousAudioCapture
from vinyl_lyrics.audio.processing import AudioMetrics, SilenceDetector, SpectrumAnalyzer
from vinyl_lyrics.config import load_config
from vinyl_lyrics.lyrics.cache import LyricsCache
from vinyl_lyrics.lyrics.lrclib import LRCLIBClient, LyricResult
from vinyl_lyrics.recognition import create_recognizer
from vinyl_lyrics.recognition.base import Track
from vinyl_lyrics.server.models import TrackOverride
from vinyl_lyrics.settings import SettingsStore, UserSettings
from vinyl_lyrics.state.player import PlayerState

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


class ApplicationRuntime:
    """Coordinate continuous capture, analysis, recognition, and clients."""

    def __init__(self, config: dict[str, Any]) -> None:
        audio_config = config.get("audio", {})
        lyrics_config = config.get("lyrics", {})
        recognition = config.get("recognition", {})
        defaults = UserSettings(
            gain_db=float(audio_config.get("gain_db", 0.0)),
            silence_enter_dbfs=float(audio_config.get("silence_enter_dbfs", -48.0)),
            silence_exit_dbfs=float(audio_config.get("silence_exit_dbfs", -54.0)),
        )
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load(defaults)
        self.capture = ContinuousAudioCapture(
            source_id=self.settings.source_id or audio_config.get("source_id"),
            sample_rate=int(audio_config.get("sample_rate", 44100)),
            channels=1,
            buffer_seconds=float(audio_config.get("buffer_seconds", 15.0)),
        )
        self.recognizer = create_recognizer(config=config)
        self.lyrics_client = LRCLIBClient(
            base_url=str(lyrics_config.get("lrclib_base_url", "https://lrclib.net")),
            user_agent=str(lyrics_config.get("user_agent", "vinyl-lyrics/0.1.0")),
        )
        self.cache = LyricsCache(lyrics_config.get("cache_path", "data/lyrics.sqlite3"))
        self.player = PlayerState(
            reidentify_interval=float(recognition.get("interval_seconds", 45))
        )
        self.silence = SilenceDetector(
            self.settings.silence_enter_dbfs,
            self.settings.silence_exit_dbfs,
            float(recognition.get("min_signal_seconds", 1.0)),
            float(recognition.get("track_gap_seconds", 1.5)),
        )
        self.spectrum = SpectrumAnalyzer(self.capture.sample_rate)
        self.clients: set[Any] = set()
        self.spectrum_clients: set[Any] = set()
        self.status = "idle"
        self.locked = False
        self.failed_attempts = 0
        self._task: asyncio.Task[None] | None = None
        self._recognition_in_progress = False
        self._next_recognition_at = 0.0
        self._last_diagnostics_at = 0.0
        self._last_metrics = AudioMetrics(0.0, 0.0, -120.0, tuple([0.0] * 64))
        logger.info(
            "Configured audio source: %s (%s), recognizer: %s",
            self.capture.source_info.name,
            self.capture.source_info.source_id,
            type(self.recognizer).__name__,
        )

    def public_state(self) -> dict[str, Any]:
        """Return the complete state event payload."""
        track = self.player.current_track
        lyrics = (
            self.cache.get_payload(track.artist, track.title, track.duration)
            if track is not None
            else None
        )
        return {
            "event": "state",
            "state": self.status,
            "track": (
                {
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "duration": track.duration,
                    "cover_url": track.cover_url,
                    "offset_seconds": track.offset_seconds,
                    "locked": self.locked,
                }
                if track
                else None
            ),
            "lyrics": lyrics,
            "elapsed_seconds": track.elapsed_seconds() if track else 0.0,
            "source": {
                "source_id": self.capture.source_info.source_id,
                "name": self.capture.source_info.name,
                "type": self.capture.source_info.source_type,
            },
        }

    async def broadcast(self, payload: dict[str, Any] | None = None) -> None:
        """Broadcast an event to state WebSocket clients."""
        message = payload or self.public_state()
        for client in tuple(self.clients):
            try:
                await client.send_json(message)
            except (ConnectionError, RuntimeError):
                self.clients.discard(client)

    async def broadcast_spectrum(self) -> None:
        """Broadcast spectrum data only when consumers are connected."""
        if not self.spectrum_clients:
            return
        metrics = self._last_metrics
        payload = {
            "bands": metrics.bands,
            "rms": metrics.rms,
            "peak": metrics.peak,
            "dbfs": metrics.dbfs,
        }
        for client in tuple(self.spectrum_clients):
            try:
                await client.send_json(payload)
            except (ConnectionError, RuntimeError):
                self.spectrum_clients.discard(client)

    def _apply_track(self, track: Track, locked: bool) -> None:
        """Apply recognized or manually selected track metadata."""
        self.player.set_track(
            track.title,
            track.artist,
            track.album,
            track.duration,
            track.cover_url,
            track.offset_seconds or self.settings.manual_offset_seconds,
        )
        self.locked = locked

    async def _load_lyrics(self, track: Track) -> None:
        cached = self.cache.get(track.artist, track.title, track.duration)
        if cached is not None:
            if getattr(self, "player", None) is not None and self.player.current_track is not None:
                self.player.current_track.duration = (
                    cached.duration or self.player.current_track.duration
                )
            return
        lyrics = await self.lyrics_client.get_lyrics(
            track.artist, track.title, track.album, track.duration
        )
        if lyrics is not None:
            if lyrics.cover_url is None:
                lyrics.cover_url = track.cover_url
            if getattr(self, "player", None) is not None and self.player.current_track is not None:
                self.player.current_track.duration = (
                    lyrics.duration or self.player.current_track.duration
                )
            self.cache.save(lyrics)
            logger.info(
                "Lyrics loaded for %s - %s (%s)",
                lyrics.artist,
                lyrics.title,
                "synced" if lyrics.synced else "plain",
            )
        else:
            logger.info("No lyrics found for %s - %s", track.artist, track.title)

    async def recognize_once(self) -> None:
        """Recognize the current buffered window."""
        if self._recognition_in_progress:
            return
        now = time.monotonic()
        if now < self._next_recognition_at:
            return
        self._recognition_in_progress = True
        self.status = "identifying"
        logger.info(
            "Starting recognition: source=%s, samples=%d, level=%.1f dBFS",
            getattr(getattr(self.capture, "source_info", None), "source_id", "unknown"),
            getattr(self.capture, "_received_samples", 0),
            getattr(getattr(self, "_last_metrics", None), "dbfs", -120.0),
        )
        await self.broadcast()
        try:
            track = await self.recognizer.recognize(self.capture.recognition_wav())
            if track is None:
                logger.info("Recognition returned no track.")
                self.failed_attempts += 1
                self.status = "idle" if self.failed_attempts >= 3 else "listening"
                self._next_recognition_at = time.monotonic() + 8.0
                await self.broadcast({"event": self.status, **self.public_state()})
                return
            logger.info(
                "Track recognized: %s - %s (offset=%s)",
                track.artist,
                track.title,
                track.offset_seconds,
            )
            self.failed_attempts = 0
            self._apply_track(track, locked=False)
            await self._load_lyrics(track)
            self.status = "playing"
            self._next_recognition_at = time.monotonic() + self.player.reidentify_interval
            await self.broadcast({"event": "track", **self.public_state()})
        finally:
            self._recognition_in_progress = False

    async def apply_settings(self, settings: UserSettings) -> UserSettings:
        """Apply and persist settings without restarting the process."""
        source_changed = settings.source_id != self.settings.source_id
        self.settings = self.settings_store.save(settings)
        self.silence.enter_dbfs = settings.silence_enter_dbfs
        self.silence.exit_dbfs = settings.silence_exit_dbfs
        if source_changed and settings.source_id:
            self.capture.switch_source(settings.source_id)
            await self.broadcast({"event": "source_changed", **self.public_state()})
        return self.settings

    async def override_track(self, override: TrackOverride) -> None:
        """Lock a manually selected track and its lyrics."""
        track = Track(
            override.title,
            override.artist,
            override.album,
            override.duration,
            override.cover_url,
            self.settings.manual_offset_seconds,
        )
        self._apply_track(track, locked=True)
        text = override.synced_lyrics or override.plain_lyrics
        if text:
            self.cache.save(
                LyricResult(
                    text=text,
                    synced=bool(override.synced_lyrics),
                    title=track.title,
                    artist=track.artist,
                    album=track.album,
                    duration=track.duration,
                    cover_url=track.cover_url,
                )
            )
        self.status = "playing"
        await self.broadcast({"event": "track", **self.public_state()})

    async def loop(self) -> None:
        """Continuously process the open source."""
        try:
            self.capture.start()
        except (OSError, RuntimeError, ValueError) as error:
            self.status = "idle"
            logger.exception("Audio source failed to start: %s", error)
            await self.broadcast({"event": "error", "state": self.status, "message": str(error)})
            return
        while True:
            try:
                samples = self.capture.latest_samples(1.0)
                self._last_metrics = self.spectrum.analyze(samples, self.settings.gain_db)
                active = self.silence.update(self._last_metrics.dbfs, 0.1)
                if (
                    active
                    and self.status in {"idle", "listening"}
                    and not self.locked
                    and time.monotonic() >= self._next_recognition_at
                ):
                    await self.recognize_once()
                elif active and self.status == "playing" and not self.locked:
                    if (
                        self.player.needs_reidentification()
                        and time.monotonic() >= self._next_recognition_at
                    ):
                        await self.recognize_once()
                elif not active and self.status == "playing":
                    if self.silence._below_for >= self.silence.min_silence_seconds:
                        self.locked = False
                        self.status = "idle"
                        await self.broadcast({"event": "idle", **self.public_state()})
                await self.broadcast_spectrum()
            except (OSError, RuntimeError, ValueError, httpx.HTTPError) as error:
                self.status = "idle"
                self._next_recognition_at = time.monotonic() + 8.0
                logger.exception("Audio processing failed: %s", error)
                await self.broadcast(
                    {"event": "error", "state": self.status, "message": str(error)}
                )
            now = time.monotonic()
            if now - self._last_diagnostics_at >= 5.0:
                self._last_diagnostics_at = now
                logger.info(
                    "Audio diagnostics: samples=%d, level=%.1f dBFS, rms=%.4f, signal=%s, state=%s",
                    self.capture._received_samples,
                    self._last_metrics.dbfs,
                    self._last_metrics.rms,
                    active,
                    self.status,
                )
            await asyncio.sleep(0.1)

    async def start(self) -> None:
        """Start continuous capture processing."""
        self._task = asyncio.create_task(self.loop())

    async def stop(self) -> None:
        """Stop processing and release the audio source."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.capture.stop()


def create_app(config: dict[str, Any] | None = None) -> FastAPI:
    """Create the FastAPI application."""
    settings = config or load_config()
    runtime = ApplicationRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = runtime
        await runtime.start()
        yield
        await runtime.stop()

    app = FastAPI(title="vinyl-lyrics", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    from vinyl_lyrics.server.api import router as api_router
    from vinyl_lyrics.server.ws import router as ws_router

    app.include_router(api_router)
    app.include_router(ws_router)
    return app


app = create_app()


def main() -> None:
    """Run the local web server."""
    config = load_config()
    server_config = config.get("server", {})
    uvicorn.run(
        "vinyl_lyrics.main:app",
        host=str(server_config.get("host", "127.0.0.1")),
        port=int(server_config.get("port", 8000)),
        reload=False,
    )


if __name__ == "__main__":
    main()
