import json
import time
from collections.abc import Iterator
from concurrent.futures import CancelledError
from contextlib import contextmanager
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import vinyl_lyrics.audio.capture as capture_module
from vinyl_lyrics.audio.capture import ContinuousAudioCapture
from vinyl_lyrics.audio.source import AudioSourceInfo, BrowserStreamSource
from vinyl_lyrics.main import create_app
from vinyl_lyrics.server.ws import AudioTokenBucket


@contextmanager
def _audio_ws(client: TestClient, **kwargs: Any) -> Iterator[Any]:
    """Open ``/ws/audio``, ignoring a cancellation raised while closing it.

    On Windows, Starlette's test client can surface the server task's
    cancellation from the closing ``__exit__``, after the block has run.
    """
    connection = client.websocket_connect("/ws/audio", **kwargs)
    ws = connection.__enter__()
    try:
        yield ws
    except BaseException as error:
        if not connection.__exit__(type(error), error, error.__traceback__):
            raise
    else:
        try:
            connection.__exit__(None, None, None)
        except CancelledError:
            pass


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


def _stream_source() -> tuple[BrowserStreamSource, list[np.ndarray]]:
    info = AudioSourceInfo("browser", "Browser", "browser")
    source = BrowserStreamSource(info, sample_rate=44100)
    received: list[np.ndarray] = []
    source.start(received.append)
    source.reset_stream("client-1", client_sample_rate=44100)
    return source, received


FRAME = np.zeros(2048, dtype=np.int16).tobytes()  # the browser worklet's block
FRAME_SECONDS = 2048 / 44100


def test_first_frame_is_anchored_at_its_recording_start() -> None:
    source, _ = _stream_source()

    source.feed_pcm16_frame(np.zeros(22050, dtype=np.int16).tobytes(), now=1000.0)

    # Half a second of audio that arrived at 1000.0 ended at 1000.0.
    assert source.current_stream_monotonic() == pytest.approx(1000.0)


def test_network_stall_and_backlog_burst_keep_the_clock() -> None:
    source, received = _stream_source()
    now = 1000.0
    for _ in range(20):
        source.feed_pcm16_frame(FRAME, now=now)
        now += FRAME_SECONDS
    # A 1.2 s Wi-Fi stall: the frames recorded meanwhile arrive in one burst.
    stalled_at = now
    now += 1.2
    backlog = int(1.2 / FRAME_SECONDS) + 1
    for index in range(backlog):
        source.feed_pcm16_frame(FRAME, now=now + index * 0.0005)
    now = stalled_at + backlog * FRAME_SECONDS
    for _ in range(20):
        source.feed_pcm16_frame(FRAME, now=now)
        now += FRAME_SECONDS

    # No silence was invented and the clock still ends at the last arrival.
    assert sum(len(chunk) for chunk in received) == (40 + backlog) * 2048
    assert source.current_stream_monotonic() == pytest.approx(now - FRAME_SECONDS, abs=0.005)


def test_sustained_lateness_is_a_real_gap() -> None:
    source, _ = _stream_source()
    now = 1000.0
    for _ in range(10):
        source.feed_pcm16_frame(FRAME, now=now)
        now += FRAME_SECONDS
    # The tab was suspended for 2 s: nothing was recorded, then normal pace.
    now += 2.0
    for _ in range(20):
        source.feed_pcm16_frame(FRAME, now=now)
        now += FRAME_SECONDS

    assert source.current_stream_monotonic() == pytest.approx(now - FRAME_SECONDS, abs=0.005)


def test_early_frame_pulls_a_late_anchor_back() -> None:
    source, _ = _stream_source()
    # The first frame was held up 300 ms; the next ones arrive on time.
    source.feed_pcm16_frame(FRAME, now=1000.3)
    for index in range(1, 10):
        source.feed_pcm16_frame(FRAME, now=1000.0 + index * FRAME_SECONDS)

    assert source.current_stream_monotonic() == pytest.approx(1000.0 + 9 * FRAME_SECONDS)


def test_anchor_follows_a_slow_sound_card() -> None:
    # Production streams delivered ~440 ppm fewer samples than wall time.
    source, _ = _stream_source()
    real_frame = FRAME_SECONDS * (1 + 440e-6)
    now = 1000.0
    for _ in range(int(600 / real_frame)):  # ten minutes
        source.feed_pcm16_frame(FRAME, now=now)
        now += real_frame

    lag = (now - real_frame) - source.current_stream_monotonic()
    assert abs(lag) < 0.02


def test_recognition_snapshot_uses_stream_clock() -> None:
    capture = ContinuousAudioCapture(source_id="browser", sample_rate=44100, buffer_seconds=15.0)
    capture.start()
    assert isinstance(capture.source, BrowserStreamSource)

    # Send 2 seconds of audio arriving at t0 = 5000.0
    capture.source.reset_stream("client-1", client_sample_rate=44100)
    samples = np.ones(44100 * 2, dtype=np.int16) * 1000
    capture.source.feed_pcm16_frame(samples.tobytes(), now=5000.0)

    # Snapshot of 1 second
    snapshot = capture.recognition_snapshot(seconds=1.0, min_seconds=0.5)
    assert snapshot is not None
    assert snapshot.sample_seconds == 1.0
    # The 2 s that arrived at 5000.0 end there; the last second starts at 4999.0
    assert pytest.approx(snapshot.sample_start_monotonic, abs=0.01) == 4999.0


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
        ws.close()

    # Socket closed: runtime transitions to no_source
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

    # 1. Missing token must not permit an unauthenticated WebSocket. On Windows,
    # Starlette can surface cancellation while cleaning up the rejected socket.
    with pytest.raises((WebSocketDisconnect, RuntimeError, CancelledError)):
        with client.websocket_connect("/ws/audio") as ws:
            ws.send_text(json.dumps({"type": "hello"}))
            ws.receive_text()

    # 2. Connect with subprotocol -> succeeds
    with _audio_ws(client, subprotocols=["vinylsync-auth.ws-secret-123"]) as ws:
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
    with _audio_ws(client) as ws:
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


def test_switch_to_server_device_disconnects_streamer(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(
        {
            "audio": {"source_id": "browser", "sample_rate": 44100},
            "server": {"host": "127.0.0.1", "port": 8000},
        }
    )
    client = TestClient(app)

    class FakeServerSource:
        def start(self, callback) -> None:
            self.started = True

        def stop(self) -> None:
            self.started = False

    fake_info = AudioSourceInfo("input:0", "Test input", "input")
    fake_source = FakeServerSource()

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

        # Keep the test deterministic: never enumerate or open the CI machine's
        # physical audio devices (Windows runners may block in native audio APIs).
        monkeypatch.setattr(capture_module, "resolve_source", lambda source_id: fake_info)
        monkeypatch.setattr(
            capture_module,
            "create_audio_source",
            lambda info, sample_rate, channels: fake_source,
        )

        # User chooses a valid server input via /api/source.
        response = client.post("/api/source", json={"source_id": "input:0"})
        assert response.status_code == 200

        # Streamer receives server_device_selected disconnect message
        disc = json.loads(ws.receive_text())
        assert disc.get("type") == "disconnected"
        assert disc.get("reason") == "server_device_selected"


def test_ws_audio_handshake_384khz() -> None:
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
                    "client_id": "hi-res-client",
                    "sample_rate": 384000,
                    "channels": 1,
                    "format": "s16le",
                }
            )
        )
        resp = json.loads(ws.receive_text())
        assert resp.get("type") == "hello_ok"
        assert app.state.runtime.status in ("listening", "idle")
