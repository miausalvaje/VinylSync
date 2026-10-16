"""HTTP API routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError

from vinyl_lyrics.audio.devices import list_audio_sources
from vinyl_lyrics.server.models import TrackOverride
from vinyl_lyrics.server.security import (
    effective_client_ip,
    get_configured_auth_token,
    is_auth_required,
    verify_token,
)
from vinyl_lyrics.server.users import (
    DEFAULT_USER_MANAGER,
    create_session_token,
    verify_session_token,
)
from vinyl_lyrics.settings import UserSettings

router = APIRouter()
WEB_ROOT = Path(__file__).resolve().parents[3] / "web"


@router.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    """Return a basic service health response."""
    from vinyl_lyrics import __version__

    return {
        "status": "ok",
        "auth_required": is_auth_required(),
        "version": __version__,
    }


@router.post("/api/auth/login")
async def login(request: Request) -> dict[str, Any]:
    """Authenticate with username/password or legacy token."""
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid request payload.")

    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    token = str(payload.get("token") or "").strip()

    peer_ip = request.client.host if request.client else None
    client_ip = effective_client_ip(
        request.headers,
        peer_ip,
        getattr(request.app.state, "trust_proxy", False),
        getattr(request.app.state, "trusted_proxies", []),
    )

    # Allow direct legacy token login if provided
    if token:
        if verify_token(token, client_ip):
            return {
                "token": token,
                "user": {"username": "token-user", "role": "admin"},
            }
        raise HTTPException(status_code=401, detail="Invalid token.")

    # Username + password login
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required.")

    try:
        user = DEFAULT_USER_MANAGER.verify_credentials(username, password)
    except ValueError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error

    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    session_token = create_session_token(user.username, user.role)
    return {
        "token": session_token,
        "user": {"username": user.username, "role": user.role},
    }


@router.post("/api/auth/register")
async def register(request: Request) -> dict[str, Any]:
    """Register a new user using an invitation code."""
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid request payload.")

    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    invite_code = str(payload.get("invite_code") or "").strip()

    if not username or not password or not invite_code:
        raise HTTPException(
            status_code=400, detail="Username, password, and invitation code are required."
        )

    try:
        user = DEFAULT_USER_MANAGER.register_user(username, password, invite_code)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    session_token = create_session_token(user.username, user.role)
    return {
        "token": session_token,
        "user": {"username": user.username, "role": user.role},
    }


def _get_current_user(request: Request) -> dict[str, Any]:
    """Extract authenticated session from request Authorization header."""
    auth_header = request.headers.get("authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    session = verify_session_token(token)
    if session:
        sub = session.get("sub", "")
        if not DEFAULT_USER_MANAGER.is_user_active(str(sub)):
            raise HTTPException(
                status_code=403, detail="User account is blocked or no longer exists."
            )
        return {"username": sub, "role": session.get("role", "user")}
    # Fallback: if legacy token is used, grant admin role
    legacy = get_configured_auth_token()
    if legacy and token == legacy:
        return {"username": "admin", "role": "admin"}
    raise HTTPException(status_code=401, detail="Authentication required.")


@router.get("/api/auth/me")
async def get_me(request: Request) -> dict[str, Any]:
    """Return currently authenticated user information."""
    if not is_auth_required():
        return {"authenticated": False, "auth_required": False, "user": None}
    try:
        user = _get_current_user(request)
        return {"authenticated": True, "auth_required": True, "user": user}
    except HTTPException:
        return {"authenticated": False, "auth_required": True, "user": None}


@router.get("/api/users")
async def list_users(request: Request) -> list[dict[str, Any]]:
    """List registered users (Admin only)."""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view users.")
    records = DEFAULT_USER_MANAGER.list_users()
    return [
        {
            "username": r.username,
            "role": r.role,
            "created_at": r.created_at,
            "is_blocked": r.is_blocked,
        }
        for r in records
    ]


@router.delete("/api/users/{username}")
async def delete_user(username: str, request: Request) -> dict[str, Any]:
    """Delete a registered user (Admin only)."""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete users.")
    try:
        deleted = DEFAULT_USER_MANAGER.delete_user(username)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"deleted": True, "username": username}


@router.post("/api/users/{username}/block")
async def block_user(username: str, request: Request) -> dict[str, Any]:
    """Block or unblock a registered user (Admin only)."""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can block/unblock users.")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    blocked = bool(payload.get("blocked", True)) if isinstance(payload, dict) else True
    try:
        updated = DEFAULT_USER_MANAGER.set_user_blocked(username, blocked)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not updated:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"username": username, "is_blocked": blocked}


@router.post("/api/invitations")
async def create_invitation(request: Request) -> dict[str, Any]:
    """Create a new invitation code (Admin only)."""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can create invitations.")
    code = DEFAULT_USER_MANAGER.create_invitation(created_by=user["username"])
    return {"code": code}


@router.get("/api/invitations")
async def list_invitations(request: Request) -> list[dict[str, Any]]:
    """List invitations (Admin only)."""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view invitations.")
    records = DEFAULT_USER_MANAGER.list_invitations()
    return [
        {
            "code": r.code,
            "created_by": r.created_by,
            "created_at": r.created_at,
            "used_by": r.used_by,
            "used_at": r.used_at,
            "is_used": r.is_used,
        }
        for r in records
    ]


@router.delete("/api/invitations/{code}")
async def delete_invitation(code: str, request: Request) -> dict[str, Any]:
    """Delete an unused invitation (Admin only)."""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete invitations.")
    deleted = DEFAULT_USER_MANAGER.delete_invitation(code)
    if not deleted:
        raise HTTPException(status_code=404, detail="Invitation not found or already used.")
    return {"deleted": True, "code": code}


def _get_request_runtime(request: Request) -> Any:
    """Resolve the ApplicationRuntime instance for the current request's session."""
    session_mgr = getattr(request.app.state, "session_manager", None)
    if session_mgr is None:
        return request.app.state.runtime
    try:
        user = _get_current_user(request)
        user_key = user.get("username") or "default"
    except HTTPException:
        user_key = "default"
    return session_mgr.get_runtime(user_key)


@router.get("/api/sources")
async def sources() -> list[dict[str, Any]]:
    """List available input and loopback sources."""
    return list_audio_sources()


@router.get("/api/state")
async def state(request: Request) -> dict[str, Any]:
    """Return the current player state."""
    runtime = _get_request_runtime(request)
    return runtime.public_state()


@router.get("/api/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    """Return persisted user settings."""
    runtime = _get_request_runtime(request)
    return runtime.settings.model_dump()


@router.put("/api/settings")
async def update_settings(request: Request) -> dict[str, Any]:
    """Validate and apply user settings immediately."""
    runtime = _get_request_runtime(request)
    try:
        settings = UserSettings.model_validate(await request.json())
        result = await runtime.apply_settings(settings)
        return result.model_dump()
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/api/settings")
async def patch_settings(request: Request) -> dict[str, Any]:
    """Merge, validate, and apply a partial settings update."""
    runtime = _get_request_runtime(request)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Settings payload must be an object.")
        current = runtime.settings.model_dump()
        settings = UserSettings.model_validate({**current, **payload})
        result = await runtime.apply_settings(settings)
        return result.model_dump()
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/source")
async def change_source(request: Request) -> dict[str, Any]:
    """Switch the audio source without restarting the application."""
    runtime = _get_request_runtime(request)
    payload = await request.json()
    source_id = payload.get("source_id")
    try:
        settings = runtime.settings.model_copy(update={"source_id": source_id})
        await runtime.apply_settings(settings)
        return runtime.public_state()
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/lyrics/search")
async def lyrics_search(request: Request, q: str = Query(min_length=1)) -> list[dict[str, Any]]:
    """Search LRCLIB for manual track correction candidates."""
    runtime = _get_request_runtime(request)
    return await runtime.lyrics_client.search(q)


@router.post("/api/track/override")
async def track_override(request: Request, override: TrackOverride) -> dict[str, Any]:
    """Lock a manually selected track."""
    runtime = _get_request_runtime(request)
    await runtime.override_track(override)
    return runtime.public_state()


@router.post("/api/track/unlock")
async def track_unlock(request: Request) -> dict[str, Any]:
    """Unlock the current track for automatic recognition."""
    runtime = _get_request_runtime(request)
    runtime.locked = False
    return runtime.public_state()


@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the local frontend."""
    return FileResponse(WEB_ROOT / "index.html")


@router.get("/favicon.svg", include_in_schema=False)
async def favicon() -> FileResponse:
    """Serve the vinyl favicon."""
    return FileResponse(WEB_ROOT / "favicon.svg", media_type="image/svg+xml")


@router.get("/css/{file_name}", include_in_schema=False)
async def stylesheet(file_name: str) -> FileResponse:
    """Serve a frontend stylesheet."""
    path = (WEB_ROOT / "css" / file_name).resolve()
    if path.parent != (WEB_ROOT / "css").resolve() or path.suffix != ".css" or not path.is_file():
        raise HTTPException(status_code=404, detail="Stylesheet not found.")
    return FileResponse(path, media_type="text/css")


@router.get("/js/{file_name}", include_in_schema=False)
async def javascript(file_name: str) -> FileResponse:
    """Serve a frontend JavaScript module."""
    path = (WEB_ROOT / "js" / file_name).resolve()
    if path.parent != (WEB_ROOT / "js").resolve() or path.suffix != ".js" or not path.is_file():
        raise HTTPException(status_code=404, detail="JavaScript file not found.")
    return FileResponse(path, media_type="text/javascript")
