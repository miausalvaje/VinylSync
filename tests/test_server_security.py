import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from vinyl_lyrics.server.security import (
    LocalSecurityMiddleware,
    allowed_hosts,
    allowed_origins,
    host_name,
)


def test_local_security_accepts_configured_and_default_hosts() -> None:
    hosts = allowed_hosts("192.168.1.20", ["turntable.local"])

    assert {"127.0.0.1", "localhost", "192.168.1.20", "turntable.local"} <= hosts
    assert host_name("localhost:8000") == "localhost"
    assert "http://turntable.local:8000" in allowed_origins(hosts, 8000)


@pytest.mark.parametrize("host", ["", "evil.example", "evil.example:8000"])
def test_unknown_hosts_are_not_in_allowed_set(host: str) -> None:
    hosts = allowed_hosts("127.0.0.1", [])

    assert host_name(host).casefold() not in hosts


def test_state_changing_requests_reject_foreign_origin() -> None:
    app = FastAPI()
    app.add_middleware(
        LocalSecurityMiddleware,
        hosts={"127.0.0.1", "localhost"},
        origins={"http://127.0.0.1:8000", "http://localhost:8000"},
    )

    @app.post("/settings")
    async def settings() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app, base_url="http://127.0.0.1:8000")
    assert client.post("/settings", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/settings", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200


def test_unknown_host_is_rejected() -> None:
    app = FastAPI()
    app.add_middleware(
        LocalSecurityMiddleware,
        hosts={"127.0.0.1", "localhost"},
        origins={"http://127.0.0.1:8000", "http://localhost:8000"},
    )
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_websocket_rejects_foreign_origin() -> None:
    hosts = {"127.0.0.1", "localhost"}
    origins = allowed_origins(hosts, 8000)

    from vinyl_lyrics.server.security import validate_websocket

    class FakeWebSocket:
        headers = {"host": "127.0.0.1:8000", "origin": "https://evil.example"}

    with pytest.raises(HTTPException) as error:
        validate_websocket(FakeWebSocket(), hosts, origins)  # type: ignore[arg-type]
    assert error.value.status_code == 403
