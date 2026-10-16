from fastapi.testclient import TestClient

import vinyl_lyrics.main as main_module


class FakeRuntime:
    def __init__(self, config) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        pass


def test_default_runtime_is_not_started_when_auth_is_enabled(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "ApplicationRuntime", FakeRuntime)
    monkeypatch.setattr(main_module, "is_auth_required", lambda: True)

    app = main_module.create_app({"server": {"host": "127.0.0.1", "port": 8000}})
    with TestClient(app):
        assert app.state.runtime.started is False
