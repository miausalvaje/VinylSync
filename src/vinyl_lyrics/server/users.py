"""User authentication and invitation management for VinylSync."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

JWT_SECRET_FILE = "data/.jwt_secret"
USERS_DB_PATH = "data/users.sqlite3"
TOKEN_EXPIRY_SECONDS = 30 * 86400  # 30 days session token


def hash_password(password: str, salt: str | None = None) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 rounds."""
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"pbkdf2:sha256:100000${salt}${key.hex()}"


def verify_password(password: str, hashed: str | None) -> bool:
    """Verify password against pbkdf2 hash, raw sha256 or sha512 hex."""
    if not hashed or not password:
        return False
    clean_hash = hashed.strip("\"' ")
    parts = clean_hash.split("$")
    if len(parts) == 3 and parts[0].startswith("pbkdf2:"):
        subparts = parts[0].split(":")
        algo = subparts[1] if len(subparts) > 1 else "sha256"
        try:
            iterations = int(subparts[2]) if len(subparts) > 2 else 100_000
        except ValueError:
            iterations = 100_000
        salt = parts[1]
        try:
            expected_key = hashlib.pbkdf2_hmac(
                algo, password.encode("utf-8"), salt.encode("utf-8"), iterations
            ).hex()
            return secrets.compare_digest(parts[2], expected_key)
        except Exception:
            return False
    # Fallback to plain sha256 (64 hex chars) or sha512 (128 hex chars)
    if len(clean_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in clean_hash):
        computed = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return secrets.compare_digest(computed.lower(), clean_hash.lower())
    if len(clean_hash) == 128 and all(c in "0123456789abcdefABCDEF" for c in clean_hash):
        computed = hashlib.sha512(password.encode("utf-8")).hexdigest()
        return secrets.compare_digest(computed.lower(), clean_hash.lower())
    return False


def get_server_secret() -> str:
    """Retrieve or generate persistent JWT secret for user session tokens."""
    env_secret = os.getenv("VINYLSYNC_SECRET_KEY")
    if env_secret and env_secret.strip():
        return env_secret.strip()
    secret_path = Path(JWT_SECRET_FILE)
    if secret_path.is_file():
        try:
            return secret_path.read_text("utf-8").strip()
        except OSError:
            pass
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_hex(32)
    try:
        secret_path.write_text(generated, "utf-8")
    except OSError:
        pass
    return generated


def create_session_token(username: str, role: str, expires_in: float = TOKEN_EXPIRY_SECONDS) -> str:
    """Generate a signed HMAC-SHA256 JWT session token."""
    secret = get_server_secret()
    payload = {
        "sub": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time() + expires_in),
    }
    header_b64 = (
        base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        .decode()
        .rstrip("=")
    )
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signing_input = f"{header_b64}.{payload_b64}".encode()
    signature = (
        base64.urlsafe_b64encode(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )
    return f"{header_b64}.{payload_b64}.{signature}"


def verify_session_token(token: str | None) -> dict[str, Any] | None:
    """Verify and decode a session token. Returns payload dict or None."""
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig = parts
    secret = get_server_secret()
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = (
        base64.urlsafe_b64encode(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )
    if not secrets.compare_digest(sig, expected_sig):
        return None
    try:
        padding = "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        data = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            return None
        exp = data.get("exp")
        if exp and time.time() > float(exp):
            return None
        return data
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class UserRecord:
    username: str
    role: str
    created_at: float


@dataclass(frozen=True, slots=True)
class InviteRecord:
    code: str
    created_by: str
    created_at: float
    used_by: str | None
    used_at: float | None

    @property
    def is_used(self) -> bool:
        return self.used_by is not None


class UserManager:
    """SQLite-backed user storage with invitation code support."""

    def __init__(self, db_path: str | Path = USERS_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS invitations (
                    code TEXT PRIMARY KEY,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    used_by TEXT,
                    used_at REAL
                )
                """
            )

    def is_auth_enabled(self) -> bool:
        """Return True if auth is active (admin in env or users exist)."""
        admin_user = (
            os.getenv("ADMIN_USERNAME") or os.getenv("VINYLSYNC_ADMIN_USER") or ""
        ).strip()
        admin_hash = (
            os.getenv("ADMIN_PASSWORD_HASH") or os.getenv("VINYLSYNC_ADMIN_PASSWORD_HASH") or ""
        ).strip()
        admin_pass = (
            os.getenv("ADMIN_PASSWORD") or os.getenv("VINYLSYNC_ADMIN_PASSWORD") or ""
        ).strip()
        if admin_user and (admin_hash or admin_pass):
            return True
        legacy_token = (os.getenv("VINYLSYNC_AUTH_TOKEN") or "").strip()
        if legacy_token:
            return True
        with self._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM users")
            row = cursor.fetchone()
            return bool(row and row[0] > 0)

    def verify_credentials(self, username: str, password: str) -> UserRecord | None:
        """Validate username & password against .env admin credentials or database."""
        clean_user = username.strip()
        if not clean_user or not password:
            return None

        # 1. Check environment admin first
        env_admin = (os.getenv("ADMIN_USERNAME") or os.getenv("VINYLSYNC_ADMIN_USER") or "").strip()
        env_hash = (
            os.getenv("ADMIN_PASSWORD_HASH") or os.getenv("VINYLSYNC_ADMIN_PASSWORD_HASH") or ""
        ).strip()
        env_pass = (
            os.getenv("ADMIN_PASSWORD") or os.getenv("VINYLSYNC_ADMIN_PASSWORD") or ""
        ).strip()

        if env_admin and clean_user.casefold() == env_admin.casefold():
            if env_hash and verify_password(password, env_hash):
                return UserRecord(username=env_admin, role="admin", created_at=0.0)
            if env_pass and secrets.compare_digest(password, env_pass):
                return UserRecord(username=env_admin, role="admin", created_at=0.0)
            return None

        # 2. Check registered users in database
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT username, password_hash, role, created_at FROM users WHERE username = ?",
                (clean_user,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            if verify_password(password, row["password_hash"]):
                return UserRecord(
                    username=row["username"],
                    role=row["role"],
                    created_at=row["created_at"],
                )
        return None

    def create_invitation(self, created_by: str) -> str:
        """Generate a secure, single-use invitation code."""
        # Clean 12-char alphanumeric invite code, e.g. 'ABCD-EFGH-JKLM'
        raw = secrets.token_hex(6).upper()
        code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO invitations (code, created_by, created_at) VALUES (?, ?, ?)",
                (code, created_by, now),
            )
        return code

    def list_invitations(self) -> list[InviteRecord]:
        """List all invitations ordered by creation date descending."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT code, created_by, created_at, used_by, used_at "
                "FROM invitations ORDER BY created_at DESC"
            )
            return [
                InviteRecord(
                    code=row["code"],
                    created_by=row["created_by"],
                    created_at=row["created_at"],
                    used_by=row["used_by"],
                    used_at=row["used_at"],
                )
                for row in cursor.fetchall()
            ]

    def register_user(self, username: str, password: str, invite_code: str) -> UserRecord:
        """Register a new user using a valid invitation code."""
        clean_user = username.strip()
        clean_code = invite_code.strip().upper()
        if not clean_user or len(clean_user) < 3 or len(clean_user) > 32:
            raise ValueError("Username must be between 3 and 32 characters.")
        if not password or len(password) < 4:
            raise ValueError("Password must be at least 4 characters.")

        env_admin = (os.getenv("ADMIN_USERNAME") or os.getenv("VINYLSYNC_ADMIN_USER") or "").strip()
        if env_admin and clean_user.casefold() == env_admin.casefold():
            raise ValueError("Username is reserved.")

        now = time.time()
        pw_hash = hash_password(password)

        with self._connect() as conn:
            # Check user doesn't already exist
            cursor = conn.execute("SELECT username FROM users WHERE username = ?", (clean_user,))
            if cursor.fetchone():
                raise ValueError("Username already taken.")

            # Validate invite code
            cursor = conn.execute(
                "SELECT code, used_by FROM invitations WHERE code = ?", (clean_code,)
            )
            invite = cursor.fetchone()
            if not invite:
                raise ValueError("Invalid invitation code.")
            if invite["used_by"] is not None:
                raise ValueError("This invitation code has already been used.")

            # Atomically register user and consume invite
            conn.execute(
                (
                    "INSERT INTO users (username, password_hash, role, created_at) "
                    "VALUES (?, ?, 'user', ?)"
                ),
                (clean_user, pw_hash, now),
            )
            conn.execute(
                "UPDATE invitations SET used_by = ?, used_at = ? WHERE code = ?",
                (clean_user, now, clean_code),
            )

        return UserRecord(username=clean_user, role="user", created_at=now)

    def delete_invitation(self, code: str) -> bool:
        """Delete an unused invitation."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM invitations WHERE code = ? AND used_by IS NULL",
                (code.strip().upper(),),
            )
            return cursor.rowcount > 0


DEFAULT_USER_MANAGER = UserManager()
