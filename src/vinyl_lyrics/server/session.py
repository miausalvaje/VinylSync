"""Multi-session management for isolated per-user / per-client audio processing."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vinyl_lyrics.main import ApplicationRuntime

logger = logging.getLogger(__name__)


class SessionManager:
    """Manage isolated ApplicationRuntime sessions per user/client."""

    def __init__(
        self,
        config: dict[str, Any],
        default_runtime: ApplicationRuntime | None = None,
    ) -> None:
        self.config = config
        self.default_runtime = default_runtime
        self.sessions: dict[str, ApplicationRuntime] = {}
        self._lock = asyncio.Lock()

        # If a default runtime was provided, register it under the default key
        if default_runtime is not None:
            self.sessions["default"] = default_runtime

    def _create_runtime(self) -> ApplicationRuntime:
        from vinyl_lyrics.main import ApplicationRuntime

        return ApplicationRuntime(self.config)

    def get_runtime(self, session_key: str | None = None) -> ApplicationRuntime:
        """Get or synchronously retrieve a runtime session."""
        key = (session_key or "").strip() or "default"
        if key in self.sessions:
            return self.sessions[key]

        # Create on demand
        runtime = self._create_runtime()
        self.sessions[key] = runtime
        return runtime

    async def get_or_create_runtime(self, session_key: str | None = None) -> ApplicationRuntime:
        """Get or asynchronously create an isolated ApplicationRuntime session."""
        key = (session_key or "").strip() or "default"
        async with self._lock:
            if key in self.sessions:
                return self.sessions[key]
            runtime = self._create_runtime()
            self.sessions[key] = runtime
            await runtime.start()
            logger.info("Created isolated audio runtime session for '%s'", key)
            return runtime

    async def cleanup_idle_sessions(self) -> None:
        """Clean up sessions that have no active connections and no capturer."""
        async with self._lock:
            keys_to_remove = []
            for key, runtime in self.sessions.items():
                if key == "default":
                    continue
                # If currently has connections or active sender, keep it
                if runtime.clients or runtime.spectrum_clients or runtime.has_capturer:
                    continue
                # If currently playing or locked track, keep it alive unless explicitly stopped
                if runtime.player.current_track is not None:
                    continue
                keys_to_remove.append(key)

            for key in keys_to_remove:
                runtime = self.sessions.pop(key, None)
                if runtime is not None:
                    logger.info("Cleaning up idle session '%s'", key)
                    await runtime.stop()

    async def stop_all(self) -> None:
        """Stop all active sessions."""
        async with self._lock:
            for key, runtime in list(self.sessions.items()):
                try:
                    await runtime.stop()
                except Exception as error:
                    logger.warning("Error stopping session '%s': %s", key, error)
            self.sessions.clear()
