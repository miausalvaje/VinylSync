"""Security helpers for the local HTTP and WebSocket server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


def allowed_hosts(configured_host: str, configured_hosts: Iterable[str] | None) -> set[str]:
    """Return normalized hostnames accepted by the local server."""
    values = {"127.0.0.1", "localhost", configured_host}
    values.update(str(value).strip() for value in configured_hosts or [])
    return {value.casefold() for value in values if value}


def host_name(host_header: str | None) -> str:
    """Extract a hostname from a Host header, including IPv6-safe parsing."""
    if not host_header:
        return ""
    return urlsplit(f"//{host_header}").hostname or ""


def allowed_origins(hosts: set[str], port: int) -> set[str]:
    """Build HTTP and WebSocket origins for accepted local hosts."""
    return {f"{scheme}://{host}:{port}" for scheme in ("http", "https") for host in hosts}


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    """Validate Host and state-changing Origin headers."""

    def __init__(self, app: Any, hosts: set[str], origins: set[str]) -> None:
        super().__init__(app)
        self.hosts = hosts
        self.origins = origins

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if host_name(request.headers.get("host")).casefold() not in self.hosts:
            return JSONResponse({"detail": "Host is not allowed."}, status_code=400)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") not in self.origins:
                return JSONResponse({"detail": "Origin is not allowed."}, status_code=403)
        return await call_next(request)


def validate_websocket(websocket: WebSocket, hosts: set[str], origins: set[str]) -> None:
    """Raise HTTP 403 when a WebSocket request is not local and same-origin."""
    if host_name(websocket.headers.get("host")).casefold() not in hosts:
        raise HTTPException(status_code=403, detail="Host is not allowed.")
    origin = websocket.headers.get("origin")
    if origin and origin.rstrip("/") not in origins:
        raise HTTPException(status_code=403, detail="Origin is not allowed.")
