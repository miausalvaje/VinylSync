"""WebSocket routes for player state, audio spectrum, and browser audio streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from vinyl_lyrics.audio.source import BrowserStreamSource
from vinyl_lyrics.server.security import (
    effective_client_ip,
    extract_ws_subprotocol,
    extract_ws_subprotocol_token,
    get_configured_auth_token,
    validate_websocket,
    verify_token,
)

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_FRAME_SIZE = 65536  # 64 KB max binary frame size


class AudioTokenBucket:
    """Token bucket rate limiter with burst tolerance for incoming audio streams."""

    def __init__(
        self,
        sample_rate: int,
        burst_multiplier: float = 2.0,
        buffer_seconds: float = 4.0,
    ) -> None:
        self.nominal_rate = sample_rate * 2  # 2 bytes per sample for mono int16
        self.fill_rate = self.nominal_rate * burst_multiplier
        self.capacity = self.nominal_rate * buffer_seconds
        self.tokens = self.capacity
        self.last_update = time.monotonic()

    def consume(self, num_bytes: int, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        elapsed = max(0.0, current - self.last_update)
        self.last_update = current
        self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
        if num_bytes > self.tokens:
            return False
        self.tokens -= num_bytes
        return True


async def authenticate_ws(websocket: WebSocket) -> bool:
    """Validate WebSocket authentication via subprotocol or first message."""
    expected = get_configured_auth_token()
    if not expected:
        return True
    peer_ip = effective_client_ip(
        websocket.headers,
        websocket.client.host if websocket.client else None,
        getattr(websocket.app.state, "trust_proxy", False),
        getattr(websocket.app.state, "trusted_proxies", []),
    )
    sub_token = extract_ws_subprotocol_token(websocket)
    if sub_token:
        if verify_token(sub_token, peer_ip):
            return True
        await websocket.close(code=1008, reason="Authentication failed")
        return False

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
        data = json.loads(raw)
        if (
            isinstance(data, dict)
            and data.get("type") == "auth"
            and verify_token(data.get("token"), peer_ip)
        ):
            await websocket.send_json({"type": "auth_ok"})
            return True
    except Exception:
        pass
    verify_token(None, peer_ip)
    await websocket.close(code=1008, reason="Authentication failed")
    return False


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Push player state updates to a connected frontend."""
    validate_websocket(
        websocket,
        websocket.app.state.allowed_hosts,
        websocket.app.state.allowed_origins,
        getattr(websocket.app.state, "trust_proxy", False),
        getattr(websocket.app.state, "trusted_proxies", []),
    )
    sub_proto, _ = extract_ws_subprotocol(websocket)
    await websocket.accept(subprotocol=sub_proto)
    if not await authenticate_ws(websocket):
        return

    runtime = websocket.app.state.runtime
    runtime.clients.add(websocket)
    try:
        await websocket.send_json(runtime.public_state())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        runtime.clients.discard(websocket)


@router.websocket("/ws/spectrum")
async def spectrum_endpoint(websocket: WebSocket) -> None:
    """Stream compact spectrum and level messages."""
    validate_websocket(
        websocket,
        websocket.app.state.allowed_hosts,
        websocket.app.state.allowed_origins,
        getattr(websocket.app.state, "trust_proxy", False),
        getattr(websocket.app.state, "trusted_proxies", []),
    )
    sub_proto, _ = extract_ws_subprotocol(websocket)
    await websocket.accept(subprotocol=sub_proto)
    if not await authenticate_ws(websocket):
        return

    runtime = websocket.app.state.runtime
    runtime.spectrum_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        runtime.spectrum_clients.discard(websocket)


@router.websocket("/ws/audio")
async def audio_stream_endpoint(websocket: WebSocket) -> None:
    """Receive live mono Int16 PCM audio from a browser streamer."""
    validate_websocket(
        websocket,
        websocket.app.state.allowed_hosts,
        websocket.app.state.allowed_origins,
        getattr(websocket.app.state, "trust_proxy", False),
        getattr(websocket.app.state, "trusted_proxies", []),
    )
    sub_proto, _ = extract_ws_subprotocol(websocket)
    await websocket.accept(subprotocol=sub_proto)
    if not await authenticate_ws(websocket):
        return

    runtime = websocket.app.state.runtime

    # 1. Expect Hello handshake
    try:
        raw_hello = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        hello = json.loads(raw_hello)
    except Exception:
        await websocket.close(code=1003, reason="Handshake timeout or invalid payload")
        return

    if not isinstance(hello, dict) or hello.get("type") != "hello":
        await websocket.close(code=1003, reason="Expected hello message")
        return

    client_id = str(hello.get("client_id") or "").strip()
    sample_rate = int(hello.get("sample_rate") or 0)
    channels = int(hello.get("channels") or 0)
    fmt = str(hello.get("format") or "")

    if not client_id or not (8000 <= sample_rate <= 96000) or channels != 1 or fmt != "s16le":
        await websocket.close(code=1003, reason="Invalid handshake parameters")
        return

    # 2. Check for busy sender and handle takeover
    if runtime.active_audio_sender is not None:
        await websocket.send_json(
            {"type": "busy", "capturer_id": runtime.active_audio_sender.client_id}
        )
        try:
            raw_next = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            next_msg = json.loads(raw_next)
        except Exception:
            await websocket.close(code=1000, reason="Sender busy")
            return

        if isinstance(next_msg, dict) and next_msg.get("type") == "takeover":
            logger.info("Browser audio sender takeover by %s", client_id)
            await runtime.disconnect_audio_sender(reason="taken_over")
        else:
            await websocket.close(code=1000, reason="Sender busy")
            return

    if runtime.capture.source_info.source_id != "browser":
        runtime.capture.switch_source("browser")
        runtime.settings = runtime.settings.model_copy(update={"source_id": "browser"})
        runtime.settings_store.save(runtime.settings)
    else:
        try:
            runtime.capture.start()
        except Exception:
            pass

    runtime.ensure_control_loop()

    from vinyl_lyrics.main import AudioSenderSession

    session = AudioSenderSession(websocket=websocket, client_id=client_id, sample_rate=sample_rate)
    runtime.active_audio_sender = session
    if isinstance(runtime.capture.source, BrowserStreamSource):
        runtime.capture.source.reset_stream(client_id, sample_rate)

    if runtime.status == "no_source":
        runtime.status = "listening"

    await websocket.send_json({"type": "hello_ok"})
    await runtime.broadcast({"event": "capturer", "has_capturer": True, "capturer_id": client_id})
    await runtime.broadcast(runtime.public_state())

    bucket = AudioTokenBucket(sample_rate=sample_rate)

    # 4. Audio frame receive loop
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            raw_bytes: bytes | None = msg.get("bytes")
            if raw_bytes:
                if len(raw_bytes) > MAX_FRAME_SIZE:
                    logger.warning("Audio frame exceeded 64KB limit: %d bytes", len(raw_bytes))
                    await websocket.close(code=1009, reason="Frame size exceeds 64KB limit")
                    break
                if not bucket.consume(len(raw_bytes)):
                    logger.warning("Audio sender %s exceeded rate limit", client_id)
                    await websocket.close(code=1008, reason="Rate limit exceeded")
                    break
                if isinstance(runtime.capture.source, BrowserStreamSource):
                    runtime.capture.source.feed_pcm16_frame(raw_bytes)
    except WebSocketDisconnect:
        pass
    except Exception as error:
        logger.warning("Error in audio stream connection: %s", error)
    finally:
        if (
            runtime.active_audio_sender is not None
            and runtime.active_audio_sender.websocket == websocket
        ):
            await runtime.disconnect_audio_sender(reason="disconnected")
