import json
from pathlib import Path

from fastapi.testclient import TestClient

from vinyl_lyrics.main import create_app
from vinyl_lyrics.server.users import (
    DEFAULT_USER_MANAGER,
    UserManager,
    create_session_token,
)


def test_isolated_sessions_between_different_users(tmp_path: Path) -> None:
    """Verify that User A and User B have completely independent player states."""
    test_db = tmp_path / "users.sqlite3"
    test_mgr = UserManager(db_path=str(test_db))
    # Register two users via invite codes
    code_a = test_mgr.create_invitation("admin")
    test_mgr.register_user("alice", "pass_a", code_a)
    code_b = test_mgr.create_invitation("admin")
    test_mgr.register_user("bob", "pass_b", code_b)

    # Patch DEFAULT_USER_MANAGER
    original_mgr = DEFAULT_USER_MANAGER
    from vinyl_lyrics.server import api, security, users

    users.DEFAULT_USER_MANAGER = test_mgr
    api.DEFAULT_USER_MANAGER = test_mgr
    security.DEFAULT_USER_MANAGER = test_mgr

    try:
        app = create_app(
            {
                "audio": {"source_id": "browser", "sample_rate": 44100},
                "server": {"host": "127.0.0.1", "port": 8000},
            }
        )
        client = TestClient(app)

        token_a = create_session_token("alice", "user")
        token_b = create_session_token("bob", "user")

        # 1. Check initial state for alice
        res_a = client.get("/api/state", headers={"Authorization": f"Bearer {token_a}"})
        assert res_a.status_code == 200
        assert res_a.json()["track"] is None

        # 2. Check initial state for bob
        res_b = client.get("/api/state", headers={"Authorization": f"Bearer {token_b}"})
        assert res_b.status_code == 200
        assert res_b.json()["track"] is None

        # 3. User A overrides their track
        track_override_payload = {
            "title": "Alice in Chains",
            "artist": "Alice",
            "album": "Dirt",
            "duration": 200.0,
        }
        res_override = client.post(
            "/api/track/override",
            json=track_override_payload,
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert res_override.status_code == 200
        assert res_override.json()["track"]["title"] == "Alice in Chains"

        # 4. User A sees their track playing
        state_a = client.get("/api/state", headers={"Authorization": f"Bearer {token_a}"}).json()
        assert state_a["track"] is not None
        assert state_a["track"]["title"] == "Alice in Chains"

        # 5. User B does NOT see User A's track; Bob's state is completely isolated!
        state_b = client.get("/api/state", headers={"Authorization": f"Bearer {token_b}"}).json()
        assert state_b["track"] is None

        # 6. User B overrides their own track with something else
        res_override_b = client.post(
            "/api/track/override",
            json={"title": "Bob Dylan Song", "artist": "Bob", "album": "Desire", "duration": 300.0},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert res_override_b.status_code == 200
        assert res_override_b.json()["track"]["title"] == "Bob Dylan Song"

        # 7. Check Alice's track remains untouched
        state_a_check = client.get(
            "/api/state", headers={"Authorization": f"Bearer {token_a}"}
        ).json()
        assert state_a_check["track"]["title"] == "Alice in Chains"

        # 8. Test WebSockets: Both users connect to /ws and receive their own respective tracks
        with client.websocket_connect("/ws", subprotocols=[f"vinylsync-auth.{token_a}"]) as ws_a:
            data_a = json.loads(ws_a.receive_text())
            assert data_a["track"]["title"] == "Alice in Chains"

        with client.websocket_connect("/ws", subprotocols=[f"vinylsync-auth.{token_b}"]) as ws_b:
            data_b = json.loads(ws_b.receive_text())
            assert data_b["track"]["title"] == "Bob Dylan Song"

    finally:
        users.DEFAULT_USER_MANAGER = original_mgr
        api.DEFAULT_USER_MANAGER = original_mgr
        security.DEFAULT_USER_MANAGER = original_mgr
