"""Application entry point and continuous runtime orchestration."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI

from vinyl_lyrics.audio.capture import ContinuousAudioCapture
from vinyl_lyrics.audio.processing import (
    AudioMetrics,
    SilenceDetector,
    SpectrumAnalyzer,
)
from vinyl_lyrics.audio.source import BrowserStreamSource
from vinyl_lyrics.config import load_config
from vinyl_lyrics.lyrics.cache import LyricsCache
from vinyl_lyrics.lyrics.lrclib import LRCLIBClient, LyricResult
from vinyl_lyrics.lyrics.multi import MultiProviderLyricsClient
from vinyl_lyrics.paths import resolve_data_path
from vinyl_lyrics.recognition import create_recognizer
from vinyl_lyrics.recognition.base import Track
from vinyl_lyrics.server.models import TrackOverride
from vinyl_lyrics.server.security import (
    LocalSecurityMiddleware,
    allowed_hosts,
    allowed_origins,
    get_configured_auth_token,
    is_auth_required,
)
from vinyl_lyrics.settings import SettingsStore, UserSettings
from vinyl_lyrics.state.player import PlayerState
from vinyl_lyrics.state.supervisor import (
    ApplyTrack,
    BroadcastEvent,
    PlaybackSupervisor,
    StartRecognition,
    SupervisorAction,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_DEBUG_WAV_KEEP = 20


def _default_user_settings(config: dict[str, Any]) -> UserSettings:
    """Build first-run settings from configured audio and UI defaults."""
    audio_config = config.get("audio", {})
    ui_config = config.get("ui", {})
    return UserSettings(
        gain_db=float(audio_config.get("gain_db", 0.0)),
        silence_enter_dbfs=float(audio_config.get("silence_enter_dbfs", -48.0)),
        silence_exit_dbfs=float(audio_config.get("silence_exit_dbfs", -54.0)),
        font_size=float(ui_config.get("font_size", 1.0)),
        manual_offset_seconds=float(ui_config.get("manual_offset_seconds", 0.0)),
    )


def _maybe_dump_debug_wav(wav_bytes: bytes, sample_seconds: float) -> Path | None:
    """Save a copy of the recognition window when debugging is enabled.

    Set VINYLSYNC_DEBUG_WAV_DIR to a directory to keep the exact audio sent
    to the recognition provider. Useful to check by ear (or with the phone
    Shazam app) whether a "no match" is an audio problem or a database gap.
    Only the newest _DEBUG_WAV_KEEP files are retained. Disabled by default.
    """
    directory = os.getenv("VINYLSYNC_DEBUG_WAV_DIR", "").strip()
    if not directory:
        return None
    try:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = target / f"recog_{stamp}_{sample_seconds:.1f}s.wav"
        counter = 0
        while path.exists():
            counter += 1
            path = target / f"recog_{stamp}_{sample_seconds:.1f}s_{counter}.wav"
        path.write_bytes(wav_bytes)
        existing = sorted(target.glob("recog_*.wav"), key=lambda item: item.stat().st_mtime)
        for old in existing[:-_DEBUG_WAV_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
        logger.debug("Saved recognition debug WAV: %s", path)
        return path
    except OSError as error:
        logger.warning("Could not save recognition debug WAV: %s", error)
        return None


@dataclass(slots=True)
class AudioSenderSession:
    """Active browser audio streaming session."""

    websocket: Any
    client_id: str
    sample_rate: int


class ApplicationRuntime:
    """Coordinate continuous capture, analysis, recognition, and clients."""

    def __init__(self, config: dict[str, Any], session_key: str | None = None) -> None:
        audio_config = config.get("audio", {})
        lyrics_config = config.get("lyrics", {})
        recognition = config.get("recognition", {})
        defaults = _default_user_settings(config)
        self.settings_store = SettingsStore(user_key=session_key)
        self.settings = self.settings_store.load(defaults)
        self.capture = ContinuousAudioCapture(
            source_id=self.settings.source_id or audio_config.get("source_id"),
            sample_rate=int(audio_config.get("sample_rate", 44100)),
            channels=1,
            buffer_seconds=float(audio_config.get("buffer_seconds", 15.0)),
        )
        self.recognizer = create_recognizer(config=config)
        lrclib_client = LRCLIBClient(
            base_url=str(lyrics_config.get("lrclib_base_url", "https://lrclib.net")),
            user_agent=str(lyrics_config.get("user_agent", "VinylSync/0.1.0")),
        )
        self.lyrics_client = MultiProviderLyricsClient(lrclib_client=lrclib_client)
        self.cache = LyricsCache(
            resolve_data_path(lyrics_config.get("cache_path", "data/lyrics.sqlite3"))
        )
        self.player = PlayerState(
            reidentify_interval=float(recognition.get("interval_seconds", 45))
        )
        self.silence = SilenceDetector(
            self.settings.silence_enter_dbfs,
            self.settings.silence_exit_dbfs,
            float(recognition.get("min_signal_seconds", 1.0)),
            float(recognition.get("track_gap_seconds", 1.5)),
        )
        self.idle_timeout_seconds = float(recognition.get("idle_timeout_seconds", 15.0))
        self.min_sample_seconds = float(recognition.get("min_sample_seconds", 5.0))
        self.recognition_window_seconds = float(recognition.get("recognition_window_seconds", 12.0))
        self.recognition_timeout_seconds = float(recognition.get("timeout_seconds", 30.0))
        self.spectrum = SpectrumAnalyzer(self.capture.sample_rate)
        self.clients: set[Any] = set()
        self.spectrum_clients: set[Any] = set()
        self.active_audio_sender: AudioSenderSession | None = None
        source_is_browser = self.capture.source_info.source_id == "browser"
        self._status = "no_source" if (self.capture.source is None or source_is_browser) else "idle"
        self._locked = False
        self._failed_attempts = 0
        self._next_recognition_at_val = 0.0
        self._signal_resumed_at_val: float | None = None
        self._track_gap_pending_val = False
        self.supervisor = PlaybackSupervisor(
            player=self.player,
            clock=time.monotonic,
            idle_timeout_seconds=self.idle_timeout_seconds,
            min_sample_seconds=self.min_sample_seconds,
            track_gap_seconds=float(recognition.get("track_gap_seconds", 1.5)),
            break_max_seconds=float(recognition.get("break_max_seconds", 6.0)),
            end_margin_seconds=float(recognition.get("end_margin_seconds", 10.0)),
            candidate_ttl_seconds=float(recognition.get("candidate_ttl_seconds", 60.0)),
            status=self._status,
        )
        self._task: asyncio.Task[None] | None = None
        self._control_task: asyncio.Task[None] | None = None
        self._spectrum_task: asyncio.Task[None] | None = None
        self._recognition_task: asyncio.Task[None] | None = None
        self._recognition_in_progress = False
        self._last_diagnostics_at = 0.0
        self._last_metrics = AudioMetrics(0.0, 0.0, -120.0, tuple([0.0] * 64))
        logger.info(
            "Configured audio source: %s (%s), recognizer: %s",
            self.capture.source_info.name,
            self.capture.source_info.source_id,
            type(self.recognizer).__name__,
        )

    @property
    def has_capturer(self) -> bool:
        return self.active_audio_sender is not None

    @property
    def capturer_id(self) -> str | None:
        return self.active_audio_sender.client_id if self.active_audio_sender else None

    def capturer_state(self) -> dict[str, Any]:
        return {
            "has_capturer": self.has_capturer,
            "capturer_id": self.capturer_id,
        }

    async def disconnect_audio_sender(self, reason: str = "disconnected") -> None:
        if self.active_audio_sender is not None:
            session = self.active_audio_sender
            self.active_audio_sender = None
            try:
                await session.websocket.send_json({"type": "disconnected", "reason": reason})
                await session.websocket.close(code=1000)
            except Exception:
                pass
            if isinstance(self.capture.source, BrowserStreamSource):
                self.capture.source.reset_stream(None, self.capture.sample_rate)
                # Drop stale audio: without this the detector keeps reading
                # the pre-disconnect buffer as live signal, masking the gap
                # (no silence edge) and misattributing old audio to new songs.
                self.capture.clear_buffer()
            if self.capture.source_info.source_id == "browser":
                self.status = "no_source"
            await self.broadcast({"event": "capturer", **self.capturer_state()})
            await self.broadcast(self.public_state())

    @property
    def status(self) -> str:
        if getattr(self, "supervisor", None) is not None:
            return self.supervisor.status
        return getattr(self, "_status", "idle")

    @status.setter
    def status(self, value: str) -> None:
        self._status = value
        if getattr(self, "supervisor", None) is not None:
            self.supervisor.status = value

    @property
    def locked(self) -> bool:
        if getattr(self, "supervisor", None) is not None:
            return self.supervisor.locked
        return getattr(self, "_locked", False)

    @locked.setter
    def locked(self, value: bool) -> None:
        self._locked = value
        if getattr(self, "supervisor", None) is not None:
            self.supervisor.locked = value

    @property
    def failed_attempts(self) -> int:
        if getattr(self, "supervisor", None) is not None:
            return self.supervisor.failed_attempts
        return getattr(self, "_failed_attempts", 0)

    @failed_attempts.setter
    def failed_attempts(self, value: int) -> None:
        self._failed_attempts = value
        if getattr(self, "supervisor", None) is not None:
            self.supervisor.failed_attempts = value

    @property
    def _next_recognition_at(self) -> float:
        if getattr(self, "supervisor", None) is not None:
            return self.supervisor.next_recognition_at
        return getattr(self, "_next_recognition_at_val", 0.0)

    @_next_recognition_at.setter
    def _next_recognition_at(self, value: float) -> None:
        self._next_recognition_at_val = value
        if getattr(self, "supervisor", None) is not None:
            self.supervisor.next_recognition_at = value

    @property
    def _signal_resumed_at(self) -> float | None:
        if getattr(self, "supervisor", None) is not None:
            return self.supervisor.signal_resumed_at
        return getattr(self, "_signal_resumed_at_val", None)

    @_signal_resumed_at.setter
    def _signal_resumed_at(self, value: float | None) -> None:
        self._signal_resumed_at_val = value
        if getattr(self, "supervisor", None) is not None:
            self.supervisor.signal_resumed_at = value

    @property
    def _track_gap_pending(self) -> bool:
        if getattr(self, "supervisor", None) is not None:
            return self.supervisor.track_gap_pending
        return getattr(self, "_track_gap_pending_val", False)

    @_track_gap_pending.setter
    def _track_gap_pending(self, value: bool) -> None:
        self._track_gap_pending_val = value
        if getattr(self, "supervisor", None) is not None:
            self.supervisor.track_gap_pending = value

    def _ensure_supervisor(self) -> PlaybackSupervisor:
        if getattr(self, "supervisor", None) is None:
            self.supervisor = PlaybackSupervisor(
                player=getattr(self, "player", None) or PlayerState(),
                status=getattr(self, "_status", "listening"),
            )
            self.supervisor.failed_attempts = getattr(self, "_failed_attempts", 0)
            self.supervisor.next_recognition_at = getattr(self, "_next_recognition_at_val", 0.0)
        return self.supervisor

    async def _execute_actions(self, actions: list[SupervisorAction]) -> None:
        for action in actions:
            if isinstance(action, StartRecognition):
                if (
                    action.cancel_current
                    and self._recognition_task is not None
                    and not self._recognition_task.done()
                ):
                    task = self._recognition_task
                    self._recognition_task = None
                    self._recognition_in_progress = False
                    task.cancel()
                if self._recognition_task is None or self._recognition_task.done():
                    self._recognition_task = asyncio.create_task(
                        self.recognize_once(
                            since=action.since,
                            min_sample_seconds=action.min_sample_seconds,
                            background=action.background,
                        )
                    )
            elif isinstance(action, BroadcastEvent):
                payload = action.payload or {"event": action.event, **self.public_state()}
                await self.broadcast(payload)
            elif isinstance(action, ApplyTrack):
                self._apply_track(action.track, locked=action.locked)
                await self._load_lyrics(action.track)

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
            # Keep the existing frontend field aligned to lyrics; playback logic uses
            # PlayerState.elapsed_seconds(), which excludes the manual lyric offset.
            "elapsed_seconds": track.lyric_elapsed_seconds() if track else 0.0,
            "playback_elapsed_seconds": track.elapsed_seconds() if track else 0.0,
            # Track seconds per wall-clock second, for extrapolating between events.
            "playback_rate": track.playback_rate if track else 1.0,
            "source": {
                "source_id": self.capture.source_info.source_id,
                "name": self.capture.source_info.name,
                "type": self.capture.source_info.source_type,
            },
            "capturer": {
                "has_capturer": self.has_capturer,
                "capturer_id": self.capturer_id,
            },
        }

    async def broadcast(self, payload: dict[str, Any] | None = None) -> None:
        """Broadcast an event to state WebSocket clients."""
        message = payload or self.public_state()
        for client in tuple(self.clients):
            try:
                await client.send_json(message)
            except Exception:
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
            except Exception:
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
                self.player.adjust_offset_if_needed(
                    detected_position, playback_rate=track.playback_rate
                )
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
            track.playback_rate,
        )
        self.locked = locked

    def _is_current_track(self, track: Track) -> bool:
        current = getattr(getattr(self, "player", None), "current_track", None)
        current_artist = getattr(current, "artist", None)
        current_title = getattr(current, "title", None)
        return (
            current is not None
            and isinstance(current_artist, str)
            and isinstance(current_title, str)
            and current_artist.casefold() == track.artist.casefold()
            and current_title.casefold() == track.title.casefold()
        )

    async def _load_lyrics(self, track: Track) -> None:
        cached = self.cache.get(track.artist, track.title, track.duration)
        if cached is not None and cached.synced:
            if self._is_current_track(track) and self.player.current_track is not None:
                self.player.current_track.duration = (
                    cached.duration or self.player.current_track.duration
                )
            return
        # A cached *plain* result stays in the cache as silent fallback, but
        # we still query the providers: a synced version may have appeared
        # (or an earlier plain pick can now be upgraded).
        try:
            lyrics = await self.lyrics_client.get_lyrics(
                track.artist, track.title, track.album, track.duration
            )
        except (httpx.HTTPError, OSError) as error:
            logger.warning(
                "Failed to fetch lyrics for %s - %s: %s", track.artist, track.title, error
            )
            lyrics = None

        if lyrics is not None:
            if lyrics.cover_url is None:
                lyrics.cover_url = track.cover_url
            if self._is_current_track(track) and self.player.current_track is not None:
                self.player.current_track.duration = (
                    lyrics.duration or self.player.current_track.duration
                )
            try:
                self.cache.save(lyrics, alias_artist=track.artist, alias_title=track.title)
            except TypeError:
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
        background: bool = False,
    ) -> None:
        """Recognize the current buffered window."""
        if self._recognition_in_progress:
            return
        now = time.monotonic()
        if now < self._next_recognition_at:
            return
        self._recognition_in_progress = True
        sup = self._ensure_supervisor()
        actions = sup.handle_recognition_start(background=background)
        logger.info(
            "Starting recognition: source=%s, samples=%d, level=%.1f dBFS",
            getattr(getattr(self.capture, "source_info", None), "source_id", "unknown"),
            getattr(self.capture, "_received_samples", 0),
            getattr(getattr(self, "_last_metrics", None), "dbfs", -120.0),
        )
        await self._execute_actions(actions)
        try:
            snapshot = self.capture.recognition_snapshot(
                since=since,
                min_seconds=min_sample_seconds,
                seconds=self.recognition_window_seconds,
            )
            if snapshot is None:
                actions = sup.handle_recognition_no_snapshot(background=background)
                await self._execute_actions(actions)
                return
            _maybe_dump_debug_wav(snapshot.wav_bytes, snapshot.sample_seconds)
            # The configured timeout applies to each provider in a fallback chain,
            # not to the entire chain; otherwise a slow primary provider could prevent
            # the fallback from ever getting a chance to identify the same sample.
            provider_count = len(getattr(self.recognizer, "providers", (self.recognizer,)))
            track = await asyncio.wait_for(
                self.recognizer.recognize(snapshot.wav_bytes),
                timeout=self.recognition_timeout_seconds * provider_count,
            )
            if track is not None and track.offset_seconds is not None:
                # The provider reports the position at the sample start; carry
                # it to now at the measured turntable speed.
                elapsed = time.monotonic() - snapshot.sample_start_monotonic
                track.offset_seconds += elapsed * (track.playback_rate or 1.0)
            if track is None:
                logger.info("Recognition returned no track.")
                actions = sup.handle_recognition_no_track(background=background)
                await self._execute_actions(actions)
                return
            logger.info(
                "Track recognized: %s - %s (offset=%s)",
                track.artist,
                track.title,
                track.offset_seconds,
            )
            actions = sup.handle_recognition_success(track, background=background)
            await self._execute_actions(actions)
        except asyncio.CancelledError:
            logger.info("Recognition task cancelled.")
            raise
        except Exception as error:
            # Broad catch on purpose: an unhandled provider error must never
            # wedge the state machine in "identifying" (which would silently
            # stop all future detection). Route everything through the normal
            # backoff path instead. (CancelledError stays fatal for shutdown.)
            logger.exception("Recognition failed: %s", error)
            actions = sup.handle_recognition_error(error, background=background)
            await self._execute_actions(actions)
        finally:
            self._recognition_in_progress = False

    async def apply_settings(self, settings: UserSettings) -> UserSettings:
        """Apply settings, switching sources before committing persisted values."""
        source_changed = settings.source_id != self.settings.source_id
        previous_source = self.capture.source
        previous_source_info = self.capture.source_info
        source_switched = False

        try:
            if source_changed and settings.source_id:
                self.capture.switch_source(settings.source_id)
                source_switched = True
            saved_settings = self.settings_store.save(settings)
        except Exception:
            # A settings write can fail after the new capture source started.
            # Restore the previous source and leave in-memory settings untouched.
            if source_switched:
                try:
                    active_source = self.capture.source
                    if active_source is not None:
                        active_source.stop()
                    self.capture.source = previous_source
                    self.capture.source_info = previous_source_info
                    if previous_source is not None:
                        previous_source.start(self.capture._on_audio)
                    self.capture.clear_buffer()
                except Exception:
                    logger.exception(
                        "Could not restore the previous audio source after settings failure"
                    )
            raise

        self.settings = saved_settings
        if (
            source_changed
            and settings.source_id != "browser"
            and self.active_audio_sender is not None
        ):
            await self.disconnect_audio_sender(reason="server_device_selected")

        self.silence.enter_dbfs = settings.silence_enter_dbfs
        self.silence.exit_dbfs = settings.silence_exit_dbfs
        self.player.set_manual_offset(settings.manual_offset_seconds)
        if source_changed and settings.source_id:
            if settings.source_id == "browser" and self.active_audio_sender is None:
                self.status = "no_source"
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
            None,  # Manual correction does not provide a recognized playback position.
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
            # Suspend/idle if no active capturer (for browser source) or no clients connected
            is_browser_source = self.capture.source_info.source_id == "browser"
            if is_browser_source and not self.has_capturer and not self.clients:
                await asyncio.sleep(0.5)
                continue
            if (
                is_browser_source
                and not self.has_capturer
                and not self.clients
                and not self.spectrum_clients
            ):
                await asyncio.sleep(0.5)
                continue

            recognition_busy = False
            try:
                samples = self.capture.latest_samples(0.25)
                level = self.spectrum.analyze_level(samples, self.settings.gain_db)
                silence = self.silence.update(level.dbfs, 0.1)
                now = time.monotonic()
                if self._recognition_task is not None and self._recognition_task.done():
                    self._recognition_task = None
                recognition_busy = (
                    self._recognition_task is not None and not self._recognition_task.done()
                )
                actions = self._ensure_supervisor().handle_silence(
                    silence,
                    recognition_busy=recognition_busy,
                )
                await self._execute_actions(actions)
            except Exception as error:
                # Broad catch: an unexpected failure (e.g. corrupt lyrics cache
                # row) must degrade to idle + backoff, never kill the control
                # loop and silently stop all future detection.
                self.status = "idle"
                self._next_recognition_at = time.monotonic() + 8.0
                logger.exception("Audio processing failed: %s", error)
                await self.broadcast(
                    {"event": "error", "state": self.status, "message": str(error)}
                )
            now = time.monotonic()
            if now - self._last_diagnostics_at >= 5.0:
                self._last_diagnostics_at = now
                supervisor = self._ensure_supervisor()
                logger.info(
                    "Audio diagnostics: samples=%d, level=%.1f dBFS, rms=%.4f, "
                    "signal=%s, state=%s, resumed=%s, recog_busy=%s, "
                    "next_in=%.1fs, failed=%d",
                    self.capture._received_samples,
                    self._last_metrics.dbfs,
                    self._last_metrics.rms,
                    silence.active,
                    self.status,
                    supervisor.signal_resumed_at is not None,
                    recognition_busy,
                    max(0.0, supervisor.next_recognition_at - now),
                    supervisor.failed_attempts,
                )
            await asyncio.sleep(0.1)

    async def spectrum_loop(self) -> None:
        """Analyze and broadcast spectrum data at approximately 40 FPS."""
        while True:
            # Sleep if no spectrum clients are connected
            if not self.spectrum_clients:
                await asyncio.sleep(0.2)
                continue
            try:
                samples = self.capture.latest_samples(2048 / self.capture.sample_rate)
                self._last_metrics = self.spectrum.analyze(samples, self.settings.gain_db)
                await self.broadcast_spectrum()
            except Exception as error:
                # Broad catch for the same reason as the control loop: a bad
                # audio chunk must not kill spectrum streaming permanently.
                logger.exception("Spectrum processing failed: %s", error)
            await asyncio.sleep(0.025)

    def ensure_control_loop(self) -> None:
        """Ensure background control and spectrum processing loops are active."""
        if self._control_task is None or self._control_task.done():
            self._control_task = asyncio.create_task(self.loop())
            self._task = self._control_task
        if self._spectrum_task is None or self._spectrum_task.done():
            self._spectrum_task = asyncio.create_task(self.spectrum_loop())

    async def start(self) -> None:
        """Start continuous capture processing."""
        self.ensure_control_loop()

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

    server_config = settings.get("server", {})
    server_host = str(server_config.get("host", "127.0.0.1"))
    server_port = int(server_config.get("port", 8000))
    trust_proxy = bool(server_config.get("trust_proxy", False))
    trusted_proxies = server_config.get("trusted_proxies")
    extra_origins = server_config.get("allowed_origins")
    hosts = allowed_hosts(server_host, server_config.get("allowed_hosts"))
    if extra_origins:
        for origin in extra_origins:
            parsed_host = urlsplit(origin).hostname
            if parsed_host:
                hosts.add(parsed_host.casefold())
    origins = allowed_origins(hosts, server_port, extra_origins)

    if server_host != "127.0.0.1" and not get_configured_auth_token():
        logger.warning(
            "Server is configured to listen on %s without an authentication token configured. "
            "Set VINYLSYNC_AUTH_TOKEN to secure remote access.",
            server_host,
        )

    from vinyl_lyrics.server.session import SessionManager

    session_manager = SessionManager(settings, default_runtime=runtime)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = runtime
        app.state.session_manager = session_manager
        app.state.allowed_hosts = hosts
        app.state.allowed_origins = origins
        app.state.trust_proxy = trust_proxy
        app.state.trusted_proxies = trusted_proxies
        # The default runtime is only used when authentication is disabled.
        # Starting it alongside authenticated per-user runtimes can open the
        # same physical input a second time without serving any authenticated
        # client, because those requests resolve to their own runtime.
        if not is_auth_required():
            await runtime.start()
        yield
        await session_manager.stop_all()

    app = FastAPI(title="VinylSync", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.session_manager = session_manager
    app.state.allowed_hosts = hosts
    app.state.allowed_origins = origins
    app.state.trust_proxy = trust_proxy
    app.state.trusted_proxies = trusted_proxies
    app.add_middleware(
        LocalSecurityMiddleware,
        hosts=hosts,
        origins=origins,
        trust_proxy=trust_proxy,
        trusted_proxies=trusted_proxies,
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
