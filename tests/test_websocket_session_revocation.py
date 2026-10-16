from vinyl_lyrics.server import ws


class FakeWebSocket:
    def __init__(self, token: str) -> None:
        self.scope = {"auth_token": token, "auth_client_ip": "127.0.0.1"}


def test_websocket_session_is_revalidated(monkeypatch) -> None:
    monkeypatch.setattr("vinyl_lyrics.server.security.is_auth_required", lambda: True)
    monkeypatch.setattr(ws, "verify_token", lambda token, client_ip: token == "active-token")

    assert ws.websocket_session_is_valid(FakeWebSocket("active-token"))  # type: ignore[arg-type]
    assert not ws.websocket_session_is_valid(FakeWebSocket("revoked-token"))  # type: ignore[arg-type]
