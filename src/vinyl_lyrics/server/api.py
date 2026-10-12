"""HTTP API routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError

from vinyl_lyrics.audio.devices import list_audio_sources
from vinyl_lyrics.server.models import TrackOverride
from vinyl_lyrics.server.security import get_configured_auth_token
from vinyl_lyrics.settings import UserSettings

router = APIRouter()
WEB_ROOT = Path(__file__).resolve().parents[3] / "web"


@router.get("/api/health")
async def health() -> dict[str, Any]:
    """Return a basic service health response."""
    return {
        "status": "ok",
        "auth_required": bool(get_configured_auth_token()),
    }


@router.get("/api/sources")
async def sources() -> list[dict[str, Any]]:
    """List available input and loopback sources."""
    return list_audio_sources()


@router.get("/api/state")
async def state(request: Request) -> dict[str, Any]:
    """Return the current player state."""
    runtime = request.app.state.runtime
    return runtime.public_state()


@router.get("/api/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    """Return persisted user settings."""
    return request.app.state.runtime.settings.model_dump()


@router.put("/api/settings")
async def update_settings(request: Request) -> dict[str, Any]:
    """Validate and apply user settings immediately."""
    try:
        settings = UserSettings.model_validate(await request.json())
        result = await request.app.state.runtime.apply_settings(settings)
        return result.model_dump()
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/api/settings")
async def patch_settings(request: Request) -> dict[str, Any]:
    """Merge, validate, and apply a partial settings update."""
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Settings payload must be an object.")
        current = request.app.state.runtime.settings.model_dump()
        settings = UserSettings.model_validate({**current, **payload})
        result = await request.app.state.runtime.apply_settings(settings)
        return result.model_dump()
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/source")
async def change_source(request: Request) -> dict[str, Any]:
    """Switch the audio source without restarting the application."""
    payload = await request.json()
    source_id = payload.get("source_id")
    try:
        settings = request.app.state.runtime.settings.model_copy(update={"source_id": source_id})
        await request.app.state.runtime.apply_settings(settings)
        return request.app.state.runtime.public_state()
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/lyrics/search")
async def lyrics_search(request: Request, q: str = Query(min_length=1)) -> list[dict[str, Any]]:
    """Search LRCLIB for manual track correction candidates."""
    return await request.app.state.runtime.lyrics_client.search(q)


@router.post("/api/track/override")
async def track_override(request: Request, override: TrackOverride) -> dict[str, Any]:
    """Lock a manually selected track."""
    await request.app.state.runtime.override_track(override)
    return request.app.state.runtime.public_state()


@router.post("/api/track/unlock")
async def track_unlock(request: Request) -> dict[str, Any]:
    """Unlock the current track for automatic recognition."""
    request.app.state.runtime.locked = False
    return request.app.state.runtime.public_state()


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
