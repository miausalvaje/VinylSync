from fastapi import FastAPI
from fastapi.testclient import TestClient

import vinyl_lyrics.server.api as api
from vinyl_lyrics.server.security import AuthRateLimiter


class RejectingUserManager:
    def verify_credentials(self, username: str, password: str) -> None:
        return None


def test_password_login_is_rate_limited(monkeypatch) -> None:
    limiter = AuthRateLimiter(max_failures=2, window_seconds=60, lockout_seconds=60)
    monkeypatch.setattr(api, "DEFAULT_RATE_LIMITER", limiter)
    monkeypatch.setattr(api, "DEFAULT_USER_MANAGER", RejectingUserManager())

    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    payload = {"username": "nobody", "password": "wrong"}
    assert client.post("/api/auth/login", json=payload).status_code == 401
    assert client.post("/api/auth/login", json=payload).status_code == 401
    assert client.post("/api/auth/login", json=payload).status_code == 429
