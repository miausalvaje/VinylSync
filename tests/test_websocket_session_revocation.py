from vinyl_lyrics.server import ws
from vinyl_lyrics.server.users import DEFAULT_USER_MANAGER


class FakeWebSocket:
    def __init__(self, token: str) -> None:
        self.scope = {"auth_token": token, "auth_client_ip": "127.0.0.1"}


def test_websocket_session_is_revalidated(monkeypatch) -> None:
    monkeypatch.setattr("vinyl_lyrics.server.security.is_auth_required", lambda: True)
    monkeypatch.setattr(
        "vinyl_lyrics.server.users.verify_session_token",
        lambda token: {"sub": "alice"} if token == "active-token" else None,
    )
    monkeypatch.setattr(
        DEFAULT_USER_MANAGER, "is_user_active", lambda username: username == "alice"
    )
    monkeypatch.setattr("vinyl_lyrics.server.security.get_configured_auth_token", lambda: None)

    assert ws.websocket_session_is_valid(FakeWebSocket("active-token"))  # type: ignore[arg-type]
    assert not ws.websocket_session_is_valid(FakeWebSocket("revoked-token"))  # type: ignore[arg-type]
