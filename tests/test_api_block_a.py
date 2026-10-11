"""API tests for Block A settings and source controls."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from vinyl_lyrics.server.api import router
from vinyl_lyrics.settings import UserSettings


class FakeRuntime:
    def __init__(self) -> None:
        self.settings = UserSettings()

    def public_state(self) -> dict[str, object]:
        return {"event": "state", "state": "idle", "track": None}

    async def apply_settings(self, settings: UserSettings) -> UserSettings:
        self.settings = settings
        return settings


def test_settings_endpoint_validates_and_applies() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.runtime = FakeRuntime()
    client = TestClient(app)

    response = client.put("/api/settings", json={"gain_db": 4.0, "font_size": 1.5})
    assert response.status_code == 200
    assert response.json()["gain_db"] == 4.0
    assert response.json()["font_size"] == 1.5

    invalid = client.put("/api/settings", json={"gain_db": 100.0})
    assert invalid.status_code == 422
