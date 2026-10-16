from vinyl_lyrics.server.users import UserManager


def test_deleted_user_is_not_active(tmp_path) -> None:
    manager = UserManager(tmp_path / "users.sqlite3")
    invite = manager.create_invitation("admin")
    manager.register_user("alice", "strong-password", invite)

    assert manager.is_user_active("alice")
    assert manager.set_user_blocked("alice", True)
    assert not manager.is_user_active("alice")

    assert manager.set_user_blocked("alice", False)
    assert manager.delete_user("alice")
    assert not manager.is_user_active("alice")


def test_environment_admin_is_active(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADMIN_USERNAME", "root")
    manager = UserManager(tmp_path / "users.sqlite3")
    assert manager.is_user_active("ROOT")
