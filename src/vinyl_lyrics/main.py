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

from vinyl_lyrics.audio.capture import ContinuousAudioCapture
from vinyl_lyrics.audio.processing import (
    AudioMetrics,
    SilenceDetector,
    SpectrumAnalyzer,
)
from vinyl_lyrics.config import load_config
from vinyl_lyrics.lyrics.cache import LyricsCache
from vinyl_lyrics.lyrics.lrclib import LRCLIBClient, LyricResult
from vinyl_lyrics.recognition import create_recognizer
from vinyl_lyrics.recognition.base import Track
from vinyl_lyrics.server.models import TrackOverride
from vinyl_lyrics.server.security import (
    LocalSecurityMiddleware,
    allowed_hosts,
    allowed_origins,
)
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
            user_agent=str(lyrics_config.get("user_agent", "VinylSync/0.1.0")),
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
        self.idle_timeout_seconds = float(recognition.get("idle_timeout_seconds", 20.0))
        self.min_sample_seconds = float(recognition.get("min_sample_seconds", 5.0))
        self.spectrum = SpectrumAnalyzer(self.capture.sample_rate)
        self.clients: set[Any] = set()
        self.spectrum_clients: set[Any] = set()
        self.status = "idle" if self.capture.source is not None else "no_source"
        self.locked = False
        self.failed_attempts = 0
        self._task: asyncio.Task[None] | None = None
        self._control_task: asyncio.Task[None] | None = None
        self._spectrum_task: asyncio.Task[None] | None = None
        self._recognition_task: asyncio.Task[None] | None = None
        self._recognition_in_progress = False
        self._next_recognition_at = 0.0
        self._last_diagnostics_at = 0.0
        self._signal_resumed_at: float | None = None
        self._track_gap_pending = False
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
        detected_position = track.offset_seconds
        current = self.player.current_track
        same_track = (
            current is not None
            and current.artist.casefold() == track.artist.casefold()
            and current.title.casefold() == track.title.casefold()
        )
        if same_track:
            if detected_position is not None:
                self.player.adjust_offset_if_needed(detected_position)
            self.locked = locked
            return
        self.player.set_track(
            track.title,
            track.artist,
            track.album,
            track.duration,
            track.cover_url,
            0.0 if detected_position is None else detected_position,
            self.settings.manual_offset_seconds,
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

    async def recognize_once(
        self,
        since: float | None = None,
        min_sample_seconds: float = 0.0,
    ) -> None:
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
            snapshot = self.capture.recognition_snapshot(
                since=since,
                min_seconds=min_sample_seconds,
            )
            if snapshot is None:
                self.status = "listening"
                return
            track = await self.recognizer.recognize(snapshot.wav_bytes)
            if track is not None and track.offset_seconds is not None:
                track.offset_seconds += time.monotonic() - snapshot.sample_start_monotonic
            if track is None:
                logger.info("Recognition returned no track.")
                self.failed_attempts += 1
                self.status = "idle" if self.failed_attempts >= 3 else "listening"
                retry_delays = (8.0, 16.0, 30.0)
                self._next_recognition_at = (
                    time.monotonic()
                    + retry_delays[min(self.failed_attempts - 1, len(retry_delays) - 1)]
                )
                await self.broadcast({"event": self.status, **self.public_state()})
                return
            logger.info(
                "Track recognized: %s - %s (offset=%s)",
                track.artist,
                track.title,
                track.offset_seconds,
            )
            self.failed_attempts = 0
            self._track_gap_pending = False
            self._signal_resumed_at = None
            self._apply_track(track, locked=False)
            await self._load_lyrics(track)
            self.status = "playing"
            self._next_recognition_at = time.monotonic() + self.player.reidentify_interval
            await self.broadcast({"event": "track", **self.public_state()})
        except (OSError, RuntimeError, ValueError, httpx.HTTPError) as error:
            self.status = "idle"
            self.failed_attempts += 1
            retry_delays = (8.0, 16.0, 30.0)
            self._next_recognition_at = (
                time.monotonic()
                + retry_delays[min(self.failed_attempts - 1, len(retry_delays) - 1)]
            )
            logger.exception("Recognition failed: %s", error)
            error_payload = self.public_state()
            error_payload.update({"event": "error", "message": str(error)})
            await self.broadcast(error_payload)
        finally:
            self._recognition_in_progress = False

    async def apply_settings(self, settings: UserSettings) -> UserSettings:
        """Apply and persist settings without restarting the process."""
        source_changed = settings.source_id != self.settings.source_id
        self.settings = self.settings_store.save(settings)
        self.silence.enter_dbfs = settings.silence_enter_dbfs
        self.silence.exit_dbfs = settings.silence_exit_dbfs
        self.player.set_manual_offset(settings.manual_offset_seconds)
        if source_changed and settings.source_id:
            self.capture.switch_source(settings.source_id)
            if self._control_task is None or self._control_task.done():
                self._control_task = asyncio.create_task(self.loop())
                self._task = self._control_task
            await self.broadcast({"event": "source_changed", **self.public_state()})
        else:
            await self.broadcast(self.public_state())
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
        """Continuously control silence detection and recognition."""
        try:
            self.capture.start()
        except (OSError, RuntimeError, ValueError) as error:
            self.status = "no_source"
            logger.exception("Audio source failed to start: %s", error)
            await self.broadcast({"event": "error", "state": self.status, "message": str(error)})
            return
        while True:
            try:
                samples = self.capture.latest_samples(0.25)
                level = self.spectrum.analyze_level(samples, self.settings.gain_db)
                silence = self.silence.update(level.dbfs, 0.1)
                now = time.monotonic()
                if self._recognition_task is not None and self._recognition_task.done():
                    self._recognition_task = None
                if silence.became_active:
                    self.failed_attempts = 0
                    self._signal_resumed_at = now
                    if self.status == "playing" or self.locked:
                        self.player.current_track = None
                        self.locked = False
                        self.status = "listening"
                        self._track_gap_pending = True
                        await self.broadcast({"event": "idle", **self.public_state()})
                if (
                    silence.active
                    and self.status in {"idle", "listening"}
                    and self._recognition_task is None
                    and now >= self._next_recognition_at
                ):
                    if self._track_gap_pending and self._signal_resumed_at is not None:
                        if now - self._signal_resumed_at >= self.min_sample_seconds:
                            self._recognition_task = asyncio.create_task(
                                self.recognize_once(
                                    since=self._signal_resumed_at,
                                    min_sample_seconds=self.min_sample_seconds,
                                )
                            )
                    else:
                        self._recognition_task = asyncio.create_task(self.recognize_once())
                elif silence.active and self.status == "playing" and not self.locked:
                    if self.player.needs_reidentification() and now >= self._next_recognition_at:
                        self._recognition_task = asyncio.create_task(self.recognize_once())
                elif not silence.active and self.status == "playing":
                    if silence.silent_for >= self.idle_timeout_seconds:
                        self.locked = False
                        self.status = "idle"
                        await self.broadcast({"event": "idle", **self.public_state()})
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
                    silence.active,
                    self.status,
                )
            await asyncio.sleep(0.1)

    async def spectrum_loop(self) -> None:
        """Analyze and broadcast spectrum data at approximately 40 FPS."""
        while True:
            try:
                samples = self.capture.latest_samples(2048 / self.capture.sample_rate)
                self._last_metrics = self.spectrum.analyze(samples, self.settings.gain_db)
                await self.broadcast_spectrum()
            except (OSError, RuntimeError, ValueError) as error:
                logger.exception("Spectrum processing failed: %s", error)
            await asyncio.sleep(0.025)

    async def start(self) -> None:
        """Start continuous capture processing."""
        self._control_task = asyncio.create_task(self.loop())
        self._spectrum_task = asyncio.create_task(self.spectrum_loop())
        self._task = self._control_task

    async def stop(self) -> None:
        """Stop processing and release the audio source."""
        tasks = [
            task
            for task in (
                self._control_task,
                self._spectrum_task,
                self._recognition_task,
            )
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.capture.stop()


def create_app(config: dict[str, Any] | None = None) -> FastAPI:
    """Create the FastAPI application."""
    settings = config or load_config()
    runtime = ApplicationRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = runtime
        app.state.allowed_hosts = hosts
        app.state.allowed_origins = allowed_origins(hosts, server_port)
        await runtime.start()
        yield
        await runtime.stop()

    server_config = settings.get("server", {})
    server_host = str(server_config.get("host", "127.0.0.1"))
    server_port = int(server_config.get("port", 8000))
    hosts = allowed_hosts(server_host, server_config.get("allowed_hosts"))
    app = FastAPI(title="VinylSync", lifespan=lifespan)
    app.state.allowed_hosts = hosts
    app.state.allowed_origins = allowed_origins(hosts, server_port)
    app.add_middleware(
        LocalSecurityMiddleware,
        hosts=hosts,
        origins=allowed_origins(hosts, server_port),
    )
    from vinyl_lyrics.server.api import router as api_router
    from vinyl_lyrics.server.ws import router as ws_router

    app.include_router(api_router)
    app.include_router(ws_router)
    return app


def main() -> None:
    """Run the local web server."""
    config = load_config()
    server_config = config.get("server", {})
    uvicorn.run(
        "vinyl_lyrics.main:create_app",
        host=str(server_config.get("host", "127.0.0.1")),
        port=int(server_config.get("port", 8000)),
        reload=False,
        factory=True,
    )


if __name__ == "__main__":
    main()
