import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from vinyl_lyrics.server.security import (
    AuthRateLimiter,
    LocalSecurityMiddleware,
    allowed_hosts,
    allowed_origins,
    effective_client_ip,
    effective_host_header,
    host_name,
    validate_websocket,
    verify_token,
)


def test_local_security_accepts_configured_and_default_hosts() -> None:
    hosts = allowed_hosts("192.168.1.20", ["turntable.local"])

    assert {"127.0.0.1", "localhost", "192.168.1.20", "turntable.local"} <= hosts
    assert host_name("localhost:8000") == "localhost"
    assert "http://turntable.local:8000" in allowed_origins(hosts, 8000)


def test_allowed_origins_includes_portless_for_proxy() -> None:
    hosts = allowed_hosts("192.168.1.20", ["turntable.local"])
    origins = allowed_origins(hosts, 8000, extra_origins=["https://custom.turntable.org/"])

    # Portless standard origins
    assert "http://turntable.local" in origins
    assert "https://turntable.local" in origins
    assert "http://turntable.local:8000" in origins
    # Extra configured origin (trailing slash stripped)
    assert "https://custom.turntable.org" in origins


@pytest.mark.parametrize("host", ["", "evil.example", "evil.example:8000"])
def test_unknown_hosts_are_not_in_allowed_set(host: str) -> None:
    hosts = allowed_hosts("127.0.0.1", [])

    assert host_name(host).casefold() not in hosts


def test_state_changing_requests_reject_foreign_origin() -> None:
    app = FastAPI()
    app.add_middleware(
        LocalSecurityMiddleware,
        hosts={"127.0.0.1", "localhost"},
        origins={"http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1"},
    )

    @app.post("/settings")
    async def settings() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app, base_url="http://127.0.0.1:8000")
    assert client.post("/settings", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/settings", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
    # Proxy without port
    assert client.post("/settings", headers={"Origin": "http://127.0.0.1"}).status_code == 200


def test_trusted_proxy_and_spoofing() -> None:
    # 1. Untrusted peer: headers must be ignored
    untrusted_headers = {
        "x-forwarded-host": "turntable.local",
        "x-forwarded-for": "10.0.0.5",
        "host": "direct.local",
    }
    assert (
        effective_host_header(
            untrusted_headers, "198.51.100.1", trust_proxy=True, trusted_proxies=["127.0.0.1"]
        )
        == "direct.local"
    )
    assert (
        effective_client_ip(
            untrusted_headers, "198.51.100.1", trust_proxy=True, trusted_proxies=["127.0.0.1"]
        )
        == "198.51.100.1"
    )

    # 2. Trusted proxy in CIDR: headers are honored
    trusted_headers = {
        "x-forwarded-host": "turntable.local",
        "x-forwarded-for": "192.168.1.100, 10.0.0.1",
        "host": "backend.internal",
    }
    assert (
        effective_host_header(
            trusted_headers, "10.0.0.1", trust_proxy=True, trusted_proxies=["10.0.0.0/8"]
        )
        == "turntable.local"
    )
    assert (
        effective_client_ip(
            trusted_headers, "10.0.0.1", trust_proxy=True, trusted_proxies=["10.0.0.0/8"]
        )
        == "192.168.1.100"
    )

    # 3. trust_proxy=False: ignored even from trusted IP
    assert (
        effective_host_header(
            trusted_headers, "10.0.0.1", trust_proxy=False, trusted_proxies=["10.0.0.0/8"]
        )
        == "backend.internal"
    )


def test_rest_authentication_and_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VINYLSYNC_AUTH_TOKEN", "secret-test-token")
    limiter = AuthRateLimiter(max_failures=3, window_seconds=10.0, lockout_seconds=10.0)

    app = FastAPI()
    app.add_middleware(
        LocalSecurityMiddleware,
        hosts={"127.0.0.1", "localhost"},
        origins={"http://127.0.0.1:8000"},
        rate_limiter=limiter,
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/state")
    async def state() -> dict[str, str]:
        return {"state": "idle"}

    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # Static / health is exempt
    assert client.get("/api/health").status_code == 200

    # Missing token -> 401
    assert client.get("/api/state").status_code == 401

    # Invalid token -> 401
    assert client.get("/api/state", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/state", headers={"Authorization": "Bearer wrong2"}).status_code == 401

    # 3rd failure locks out IP -> 429
    assert client.get("/api/state", headers={"Authorization": "Bearer wrong3"}).status_code == 429
    # Even valid token gets 429 when locked out
    assert (
        client.get("/api/state", headers={"Authorization": "Bearer secret-test-token"}).status_code
        == 429
    )

    # Reset limiter, test success
    limiter.record_success("127.0.0.1")
    limiter.record_success("testclient")
    res = client.get("/api/state", headers={"Authorization": "Bearer secret-test-token"})
    assert res.status_code == 200
    assert res.json() == {"state": "idle"}


def test_verify_token_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VINYLSYNC_AUTH_TOKEN", "valid-token")
    limiter = AuthRateLimiter(max_failures=2, window_seconds=5.0, lockout_seconds=5.0)

    assert verify_token("valid-token", "1.2.3.4", limiter) is True
    assert verify_token("wrong", "1.2.3.4", limiter) is False
    assert verify_token("wrong2", "1.2.3.4", limiter) is False
    assert limiter.is_locked("1.2.3.4") is True
    assert verify_token("valid-token", "1.2.3.4", limiter) is False


def test_websocket_rejects_foreign_origin() -> None:
    hosts = {"127.0.0.1", "localhost"}
    origins = allowed_origins(hosts, 8000)

    class FakeWebSocket:
        headers = {"host": "127.0.0.1:8000", "origin": "https://evil.example"}
        client = None

    with pytest.raises(HTTPException) as error:
        validate_websocket(FakeWebSocket(), hosts, origins)  # type: ignore[arg-type]
    assert error.value.status_code == 403


def test_extract_ws_subprotocol_matches_exact_string() -> None:
    from vinyl_lyrics.server.security import extract_ws_subprotocol

    class FakeWebSocket:
        headers = {"sec-websocket-protocol": "vinylsync-auth.my-secret-key-123"}
        scope = {"subprotocols": ["vinylsync-auth.my-secret-key-123"]}

    proto, token = extract_ws_subprotocol(FakeWebSocket())  # type: ignore[arg-type]
    assert proto == "vinylsync-auth.my-secret-key-123"
    assert token == "my-secret-key-123"


def test_lan_hosts_allowed_when_listening_on_all_interfaces() -> None:
    from vinyl_lyrics.server.security import allowed_hosts, validate_websocket

    hosts = allowed_hosts("0.0.0.0", None)
    origins = {"http://127.0.0.1:8000"}

    # LAN client accessing via IP or .local
    class FakeLanWebSocket:
        headers = {"host": "192.168.1.150:8000", "origin": "http://192.168.1.150:8000"}
        client = None

    # Should not raise 403
    validate_websocket(FakeLanWebSocket(), hosts, origins)  # type: ignore[arg-type]
