import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vinyl_lyrics.audio.capture import ContinuousAudioCapture
from vinyl_lyrics.audio.source import AudioSourceInfo, BrowserStreamSource
from vinyl_lyrics.main import create_app
from vinyl_lyrics.server.ws import AudioTokenBucket


def test_audio_token_bucket_allows_legitimate_burst() -> None:
    bucket = AudioTokenBucket(sample_rate=44100, burst_multiplier=2.0, buffer_seconds=4.0)
    nominal_1s = 44100 * 2  # 88,200 bytes

    # Burst of 2 seconds sent instantaneously (< 4s capacity)
    burst_bytes = nominal_1s * 2
    assert bucket.consume(burst_bytes, now=100.0) is True

    # Immediate excessive flood exceeding remaining capacity
    assert bucket.consume(nominal_1s * 3, now=100.0) is False

    # After waiting 2 seconds at 2x fill rate, tokens recover
    assert bucket.consume(nominal_1s, now=102.0) is True


def test_browser_stream_source_resampling() -> None:
    info = AudioSourceInfo("browser", "Browser", "browser")
    source = BrowserStreamSource(info, sample_rate=44100)
    received: list[np.ndarray] = []
    source.start(received.append)

    # Set client sample rate to 48000 Hz
    source.reset_stream("client-1", client_sample_rate=48000)

    # Send 480 samples at 48000 Hz (= 10 ms of audio)
    t = np.linspace(0, 0.01, 480, endpoint=False)
    tone = (np.sin(2 * np.pi * 440 * t) * 30000).astype(np.int16)
    source.feed_pcm16_frame(tone.tobytes(), now=1000.0)

    assert len(received) == 1
    # 480 samples at 48kHz resampled to 44.1kHz = 441 samples
    assert len(received[0]) == 441


def test_browser_stream_source_synchronization_bursts_and_gaps() -> None:
    """Buffer time must derive from samples received (anchored to first arrival).

    Gaps must be zero-filled, and rapid bursts must not drift the calculated position.
    """
    info = AudioSourceInfo("browser", "Browser", "browser")
    source = BrowserStreamSource(info, sample_rate=44100)
    received: list[np.ndarray] = []
    source.start(received.append)

    source.reset_stream("client-1", client_sample_rate=44100)

    # 1. First frame of 0.5s arrives at t0 = 1000.0
    frame_0_5s = (np.zeros(22050, dtype=np.int16)).tobytes()
    source.feed_pcm16_frame(frame_0_5s, now=1000.0)

    # Monotonic end should be t0 + 0.5s = 1000.5
    assert pytest.approx(source.current_stream_monotonic(), abs=1e-3) == 1000.5

    # 2. GAP: 0.3s of real time pass without frames (e.g. dropped network packet)
    # Next frame arrives at t = 1000.8 (expected was 1000.5, gap = 0.3s)
    source.feed_pcm16_frame(frame_0_5s, now=1000.8)

    # Source should have filled gap with zeros (0.3s = 13230 samples), plus delivered 0.5s frame
    # Total stream duration: 0.5s (frame 1) + 0.3s (gap) + 0.5s (frame 2) = 1.3s
    expected_end = 1000.0 + 1.3
    assert pytest.approx(source.current_stream_monotonic(), abs=0.02) == expected_end

    # 3. BURST: Two frames arrive in rapid succession (within 1 ms) due to buffer flushing
    source.feed_pcm16_frame(frame_0_5s, now=1000.801)
    source.feed_pcm16_frame(frame_0_5s, now=1000.802)

    # Stream duration adds 1.0s cleanly without jumping back or forward based on arrival timestamp
    expected_end += 1.0
    assert pytest.approx(source.current_stream_monotonic(), abs=0.02) == expected_end


def test_recognition_snapshot_uses_stream_clock() -> None:
    capture = ContinuousAudioCapture(source_id="browser", sample_rate=44100, buffer_seconds=15.0)
    capture.start()
    assert isinstance(capture.source, BrowserStreamSource)

    # Send 2 seconds of audio anchored at t0 = 5000.0
    capture.source.reset_stream("client-1", client_sample_rate=44100)
    samples = np.ones(44100 * 2, dtype=np.int16) * 1000
    capture.source.feed_pcm16_frame(samples.tobytes(), now=5000.0)

    # Snapshot of 1 second
    snapshot = capture.recognition_snapshot(seconds=1.0, min_seconds=0.5)
    assert snapshot is not None
    assert snapshot.sample_seconds == 1.0
    # End of stream is 5000.0 + 2.0 = 5002.0
    # Sample start monotonic should be 5002.0 - 1.0 = 5001.0
    assert pytest.approx(snapshot.sample_start_monotonic, abs=0.01) == 5001.0


def test_ws_audio_handshake_and_tone_processing() -> None:
    app = create_app(
        {
            "audio": {"source_id": "browser", "sample_rate": 44100},
            "server": {"host": "127.0.0.1", "port": 8000},
        }
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/audio") as ws:
        # Invalid handshake
        ws.send_text(json.dumps({"type": "hello", "sample_rate": 1000}))
        # Invalid rate should close socket
        # Next receive will indicate closure
        with pytest.raises((WebSocketDisconnect, RuntimeError)):
            ws.receive_text()

    # Valid handshake and tone streaming
    with client.websocket_connect("/ws/audio") as ws:
        ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "client_id": "test-client-1",
                    "sample_rate": 44100,
                    "channels": 1,
                    "format": "s16le",
                }
            )
        )
        resp = json.loads(ws.receive_text())
        assert resp.get("type") == "hello_ok"

        # Stream a synthetic 440 Hz tone
        sample_rate = 44100
        t = np.linspace(0, 0.5, int(sample_rate * 0.5), endpoint=False)
        tone = (np.sin(2 * np.pi * 440 * t) * 20000).astype(np.int16)
        ws.send_bytes(tone.tobytes())

        # Give runtime a moment to process frame into circular buffer
        time.sleep(0.05)
        runtime = app.state.runtime
        samples = runtime.capture.latest_samples(0.25)
        metrics = runtime.spectrum.analyze(samples, 0.0)
        assert metrics.dbfs > -30.0  # Clear signal detected
        assert metrics.rms > 0.1


def test_ws_audio_busy_and_takeover() -> None:
    app = create_app(
        {
            "audio": {"source_id": "browser", "sample_rate": 44100},
            "server": {"host": "127.0.0.1", "port": 8000},
        }
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/audio") as ws1:
        ws1.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "client_id": "client-alpha",
                    "sample_rate": 44100,
                    "channels": 1,
                    "format": "s16le",
                }
            )
        )
        assert json.loads(ws1.receive_text()).get("type") == "hello_ok"
        assert app.state.runtime.capturer_id == "client-alpha"

        # Client 2 connects while Client 1 is active
        with client.websocket_connect("/ws/audio") as ws2:
            ws2.send_text(
                json.dumps(
                    {
                        "type": "hello",
                        "client_id": "client-beta",
                        "sample_rate": 44100,
                        "channels": 1,
                        "format": "s16le",
                    }
                )
            )
            busy_resp = json.loads(ws2.receive_text())
            assert busy_resp.get("type") == "busy"
            assert busy_resp.get("capturer_id") == "client-alpha"

            # Client 2 initiates takeover
            ws2.send_text(json.dumps({"type": "takeover"}))
            takeover_resp = json.loads(ws2.receive_text())
            assert takeover_resp.get("type") == "hello_ok"
            assert app.state.runtime.capturer_id == "client-beta"

            # Client 1 should receive disconnected message
            disc = json.loads(ws1.receive_text())
            assert disc.get("type") == "disconnected"
            assert disc.get("reason") == "taken_over"


def test_ws_audio_disconnect_transitions_to_no_source() -> None:
    app = create_app(
        {
            "audio": {"source_id": "browser", "sample_rate": 44100},
            "server": {"host": "127.0.0.1", "port": 8000},
        }
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/audio") as ws:
        ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "client_id": "temp-client",
                    "sample_rate": 44100,
                    "channels": 1,
                    "format": "s16le",
                }
            )
        )
        assert json.loads(ws.receive_text()).get("type") == "hello_ok"
        assert app.state.runtime.status in ("listening", "idle")

    # Socket closed: runtime transitions to no_source
    time.sleep(0.05)
    assert app.state.runtime.status == "no_source"
    assert app.state.runtime.has_capturer is False


def test_ws_audio_rate_limit_and_frame_size_enforcement() -> None:
    app = create_app(
        {
            "audio": {"source_id": "browser", "sample_rate": 44100},
            "server": {"host": "127.0.0.1", "port": 8000},
        }
    )
    client = TestClient(app)

    # 1. Frame exceeding 64 KB is rejected
    with client.websocket_connect("/ws/audio") as ws:
        ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "client_id": "test-limit",
                    "sample_rate": 44100,
                    "channels": 1,
                    "format": "s16le",
                }
            )
        )
        assert json.loads(ws.receive_text()).get("type") == "hello_ok"
        oversized = bytes(70000)
        ws.send_bytes(oversized)
        with pytest.raises((WebSocketDisconnect, RuntimeError)):
            ws.receive_text()

    # 2. Rate limit flood abuse is rejected
    with client.websocket_connect("/ws/audio") as ws:
        ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "client_id": "test-flood",
                    "sample_rate": 44100,
                    "channels": 1,
                    "format": "s16le",
                }
            )
        )
        assert json.loads(ws.receive_text()).get("type") == "hello_ok"
        # Send 10 chunks of 50 KB in 0 seconds = 500 KB (capacity is ~352 KB)
        chunk = bytes(50000)
        with pytest.raises((WebSocketDisconnect, RuntimeError)):
            for _ in range(12):
                ws.send_bytes(chunk)
            ws.receive_text()


def test_ws_audio_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VINYLSYNC_AUTH_TOKEN", "ws-secret-123")
    app = create_app(
        {
            "audio": {"source_id": "browser", "sample_rate": 44100},
            "server": {"host": "127.0.0.1", "port": 8000},
        }
    )
    client = TestClient(app)

    # 1. Connect without token -> fails on first message check
    with client.websocket_connect("/ws/audio") as ws:
        ws.send_text(json.dumps({"type": "hello"}))
        with pytest.raises((WebSocketDisconnect, RuntimeError)):
            ws.receive_text()

    # 2. Connect with subprotocol -> succeeds
    with client.websocket_connect("/ws/audio", subprotocols=["vinylsync-auth.ws-secret-123"]) as ws:
        assert getattr(ws, "accepted_subprotocol", None) == "vinylsync-auth.ws-secret-123"
        ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "client_id": "auth-client-1",
                    "sample_rate": 44100,
                    "channels": 1,
                    "format": "s16le",
                }
            )
        )
        assert json.loads(ws.receive_text()).get("type") == "hello_ok"

    # 3. Connect with first auth message -> succeeds
    with client.websocket_connect("/ws/audio") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "ws-secret-123"}))
        auth_resp = json.loads(ws.receive_text())
        assert auth_resp.get("type") == "auth_ok"
        ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "client_id": "auth-client-2",
                    "sample_rate": 44100,
                    "channels": 1,
                    "format": "s16le",
                }
            )
        )
        assert json.loads(ws.receive_text()).get("type") == "hello_ok"


def test_switch_to_server_device_disconnects_streamer() -> None:
    app = create_app(
        {
            "audio": {"source_id": "browser", "sample_rate": 44100},
            "server": {"host": "127.0.0.1", "port": 8000},
        }
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/audio") as ws:
        ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "client_id": "streamer-1",
                    "sample_rate": 44100,
                    "channels": 1,
                    "format": "s16le",
                }
            )
        )
        assert json.loads(ws.receive_text()).get("type") == "hello_ok"
        assert app.state.runtime.has_capturer is True

        # User chooses a server device via /api/source
        client.post("/api/source", json={"source_id": "unavailable"})
        # Streamer receives server_device_selected disconnect message
        disc = json.loads(ws.receive_text())
        assert disc.get("type") == "disconnected"
        assert disc.get("reason") == "server_device_selected"
