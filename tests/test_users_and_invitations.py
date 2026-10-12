"""Tests for user authentication and invitation management."""

import hashlib
import os
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vinyl_lyrics.main import create_app
from vinyl_lyrics.server.users import (
    UserManager,
    create_session_token,
    hash_password,
    verify_password,
    verify_session_token,
)


def test_password_hashing_and_verification() -> None:
    pw = "SecretAdmin123"
    hashed = hash_password(pw)
    assert verify_password(pw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False

    # Plain sha256 verification fallback
    sha256_hash = hashlib.sha256(pw.encode()).hexdigest()
    assert verify_password(pw, sha256_hash) is True
    assert verify_password("WrongPassword", sha256_hash) is False


def test_jwt_session_token_roundtrip() -> None:
    token = create_session_token("admin", "admin", expires_in=3600)
    decoded = verify_session_token(token)
    assert decoded is not None
    assert decoded["sub"] == "admin"
    assert decoded["role"] == "admin"

    # Invalid token string
    assert verify_session_token("invalid.token.signature") is None


def test_user_manager_invitation_and_registration(tmp_path: Path) -> None:
    db_file = tmp_path / "test_users.sqlite3"
    manager = UserManager(db_path=db_file)

    # Initially no users, auth disabled if no env vars
    assert manager.is_auth_enabled() is False

    # Create invitation
    invite_code = manager.create_invitation("admin")
    assert invite_code is not None
    assert len(invite_code) == 14  # XXXX-XXXX-XXXX

    # Register user with invite
    user = manager.register_user("carlos", "my_password", invite_code)
    assert user.username == "carlos"
    assert user.role == "user"

    # User is now registered and authenticates
    verified = manager.verify_credentials("carlos", "my_password")
    assert verified is not None
    assert verified.username == "carlos"
    assert manager.verify_credentials("carlos", "wrong_password") is None

    # Invite cannot be reused
    with pytest.raises(ValueError, match="already been used"):
        manager.register_user("other_user", "pass", invite_code)

    # Duplicate username rejected
    new_invite = manager.create_invitation("admin")
    with pytest.raises(ValueError, match="already taken"):
        manager.register_user("carlos", "pass", new_invite)


def test_api_user_auth_and_invitations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "test_api_users.sqlite3"
    test_manager = UserManager(db_path=db_file)
    monkeypatch.setattr("vinyl_lyrics.server.users.DEFAULT_USER_MANAGER", test_manager)
    monkeypatch.setattr("vinyl_lyrics.server.api.DEFAULT_USER_MANAGER", test_manager)

    # Configure Admin in environment
    admin_pw = "SuperAdminPassword"
    admin_hash = hash_password(admin_pw)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", admin_hash)

    app = create_app({"server": {"host": "127.0.0.1", "port": 8000}})
    client = TestClient(app)

    # 1. Health check reports auth_required
    health = client.get("/api/health").json()
    assert health["auth_required"] is True

    # 2. Protected routes return 401 without token
    assert client.get("/api/state").status_code == 401

    # 3. Invalid login fails
    login_fail = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert login_fail.status_code == 401

    # 4. Valid Admin login succeeds
    login_ok = client.post("/api/auth/login", json={"username": "admin", "password": admin_pw})
    assert login_ok.status_code == 200
    admin_token = login_ok.json()["token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 5. Access protected route with Admin token
    state_res = client.get("/api/state", headers=admin_headers)
    assert state_res.status_code == 200

    # 6. Admin creates an invitation
    inv_res = client.post("/api/invitations", headers=admin_headers)
    assert inv_res.status_code == 200
    code = inv_res.json()["code"]

    # 7. List invitations
    list_res = client.get("/api/invitations", headers=admin_headers)
    assert list_res.status_code == 200
    assert any(inv["code"] == code for inv in list_res.json())

    # 8. New user registers with the invitation
    reg_res = client.post(
        "/api/auth/register",
        json={"invite_code": code, "username": "invited_guest", "password": "guestpassword123"},
    )
    assert reg_res.status_code == 200
    guest_token = reg_res.json()["token"]
    guest_headers = {"Authorization": f"Bearer {guest_token}"}

    # 9. Guest can access app state
    guest_state = client.get("/api/state", headers=guest_headers)
    assert guest_state.status_code == 200

    # 10. Guest cannot create invitations (Admin only)
    guest_inv = client.post("/api/invitations", headers=guest_headers)
    assert guest_inv.status_code == 403
