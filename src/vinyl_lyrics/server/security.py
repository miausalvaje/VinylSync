"""Security helpers for the local HTTP and WebSocket server."""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


def is_ip_in_networks(ip_str: str, networks: Iterable[str]) -> bool:
    """Check if an IP address falls within any of the specified networks or IPs."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for net_str in networks:
        try:
            net = ipaddress.ip_network(net_str, strict=False)
            if ip in net:
                return True
        except ValueError:
            continue
    return False


def allowed_hosts(configured_host: str, configured_hosts: Iterable[str] | None) -> set[str]:
    """Return normalized hostnames accepted by the local server."""
    values = {"127.0.0.1", "localhost", "testserver", configured_host}
    values.update(str(value).strip() for value in configured_hosts or [])
    return {value.casefold() for value in values if value}


def host_name(host_header: str | None) -> str:
    """Extract a hostname from a Host header, including IPv6-safe parsing."""
    if not host_header:
        return ""
    return urlsplit(f"//{host_header}").hostname or ""


def is_local_or_private_host(host: str) -> bool:
    """Return True if host is localhost, a local domain, or a private/link-local IP address."""
    if not host:
        return False
    clean_host = host.casefold().strip("[]")
    if clean_host in {"localhost", "127.0.0.1", "::1", "testserver"}:
        return True
    if clean_host.endswith((".local", ".lan", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(clean_host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def allowed_origins(
    hosts: set[str], port: int, extra_origins: Iterable[str] | None = None
) -> set[str]:
    """Build HTTP and WebSocket origins for accepted hosts, including proxy default ports."""
    origins: set[str] = set()
    for host in hosts:
        for scheme in ("http", "https"):
            origins.add(f"{scheme}://{host}")
            origins.add(f"{scheme}://{host}:{port}")
    if extra_origins:
        for origin in extra_origins:
            cleaned = str(origin).strip().rstrip("/")
            if cleaned:
                origins.add(cleaned)
    return origins


def effective_client_ip(
    headers: Any,
    peer_ip: str | None,
    trust_proxy: bool,
    trusted_proxies: Iterable[str] | None,
) -> str:
    """Extract client IP, honoring X-Forwarded-For only when peer is in trusted_proxies."""
    if trust_proxy and trusted_proxies and peer_ip and is_ip_in_networks(peer_ip, trusted_proxies):
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            return str(forwarded).split(",")[0].strip()
    return peer_ip or "127.0.0.1"


def effective_host_header(
    headers: Any,
    peer_ip: str | None,
    trust_proxy: bool,
    trusted_proxies: Iterable[str] | None,
) -> str:
    """Extract Host header, honoring X-Forwarded-Host only when peer is in trusted_proxies."""
    if trust_proxy and trusted_proxies and peer_ip and is_ip_in_networks(peer_ip, trusted_proxies):
        forwarded = headers.get("x-forwarded-host")
        if forwarded:
            return str(forwarded).split(",")[0].strip()
    return headers.get("host") or ""


class AuthRateLimiter:
    """In-memory rate limiter for failed authentication attempts."""

    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: float = 60.0,
        lockout_seconds: float = 60.0,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._lockouts: dict[str, float] = {}

    def is_locked(self, ip: str, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return current < self._lockouts.get(ip, 0.0)

    def record_failure(self, ip: str, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        timestamps = self._failures.setdefault(ip, [])
        cutoff = current - self.window_seconds
        timestamps[:] = [t for t in timestamps if t >= cutoff]
        timestamps.append(current)
        if len(timestamps) >= self.max_failures:
            self._lockouts[ip] = current + self.lockout_seconds
            return True
        return False

    def record_success(self, ip: str) -> None:
        self._failures.pop(ip, None)
        self._lockouts.pop(ip, None)


DEFAULT_RATE_LIMITER = AuthRateLimiter()


def get_configured_auth_token() -> str | None:
    """Return the configured authentication token from the environment, if any."""
    token = os.getenv("VINYLSYNC_AUTH_TOKEN")
    return token.strip() if token and token.strip() else None


def verify_token(
    provided_token: str | None,
    client_ip: str = "127.0.0.1",
    rate_limiter: AuthRateLimiter | None = None,
) -> bool:
    """Verify an authentication token in constant time with optional rate limiting."""
    expected = get_configured_auth_token()
    if not expected:
        return True
    limiter = rate_limiter or DEFAULT_RATE_LIMITER
    if limiter.is_locked(client_ip):
        return False
    if not provided_token:
        limiter.record_failure(client_ip)
        return False
    is_valid = secrets.compare_digest(provided_token, expected)
    if is_valid:
        limiter.record_success(client_ip)
    else:
        limiter.record_failure(client_ip)
    return is_valid


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    """Validate Host, state-changing Origin headers, and optional authentication."""

    def __init__(
        self,
        app: Any,
        hosts: set[str],
        origins: set[str],
        trust_proxy: bool = False,
        trusted_proxies: list[str] | None = None,
        rate_limiter: AuthRateLimiter | None = None,
    ) -> None:
        super().__init__(app)
        self.hosts = hosts
        self.origins = origins
        self.trust_proxy = trust_proxy
        self.trusted_proxies = trusted_proxies or []
        self.rate_limiter = rate_limiter or DEFAULT_RATE_LIMITER

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        peer_ip = request.client.host if request.client else None
        host_hdr = effective_host_header(
            request.headers, peer_ip, self.trust_proxy, self.trusted_proxies
        )
        hdr_host = host_name(host_hdr).casefold()
        host_allowed = (
            "*" in self.hosts
            or hdr_host in self.hosts
            or ("0.0.0.0" in self.hosts and is_local_or_private_host(hdr_host))
            or ("::" in self.hosts and is_local_or_private_host(hdr_host))
        )
        if not host_allowed:
            return JSONResponse({"detail": "Host is not allowed."}, status_code=400)

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                cleaned_origin = origin.rstrip("/")
                origin_host = urlsplit(origin).hostname or ""
                origin_allowed = (
                    "*" in self.origins
                    or cleaned_origin in self.origins
                    or ("0.0.0.0" in self.hosts and is_local_or_private_host(origin_host))
                    or ("::" in self.hosts and is_local_or_private_host(origin_host))
                )
                if not origin_allowed:
                    return JSONResponse({"detail": "Origin is not allowed."}, status_code=403)

        expected_token = get_configured_auth_token()
        if expected_token:
            path = request.url.path
            is_exempt = path in {"/", "/favicon.svg", "/api/health"} or path.startswith(
                ("/css/", "/js/")
            )
            if not is_exempt:
                client_ip = effective_client_ip(
                    request.headers, peer_ip, self.trust_proxy, self.trusted_proxies
                )
                if self.rate_limiter.is_locked(client_ip):
                    return JSONResponse(
                        {"detail": "Too many failed attempts. Try again later."},
                        status_code=429,
                    )
                auth_header = request.headers.get("authorization", "")
                token = ""
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:].strip()
                if not verify_token(token, client_ip, self.rate_limiter):
                    return JSONResponse(
                        {"detail": "Invalid or missing authentication token."},
                        status_code=401,
                        headers={"WWW-Authenticate": "Bearer"},
                    )

        return await call_next(request)


def validate_websocket(
    websocket: WebSocket,
    hosts: set[str],
    origins: set[str],
    trust_proxy: bool = False,
    trusted_proxies: list[str] | None = None,
) -> None:
    """Raise HTTP 403 when a WebSocket request has an untrusted host or origin."""
    peer_ip = websocket.client.host if websocket.client else None
    host_hdr = effective_host_header(websocket.headers, peer_ip, trust_proxy, trusted_proxies or [])
    hdr_host = host_name(host_hdr).casefold()
    host_allowed = (
        "*" in hosts
        or hdr_host in hosts
        or ("0.0.0.0" in hosts and is_local_or_private_host(hdr_host))
        or ("::" in hosts and is_local_or_private_host(hdr_host))
    )
    if not host_allowed:
        raise HTTPException(status_code=403, detail="Host is not allowed.")
    origin = websocket.headers.get("origin")
    if origin:
        cleaned_origin = origin.rstrip("/")
        origin_host = urlsplit(origin).hostname or ""
        origin_allowed = (
            "*" in origins
            or cleaned_origin in origins
            or ("0.0.0.0" in hosts and is_local_or_private_host(origin_host))
            or ("::" in hosts and is_local_or_private_host(origin_host))
        )
        if not origin_allowed:
            raise HTTPException(status_code=403, detail="Origin is not allowed.")


def extract_ws_subprotocol(websocket: WebSocket) -> tuple[str | None, str | None]:
    """Extract matching requested subprotocol and auth token if present.

    Returns:
        (matched_subprotocol, extracted_token)
    """
    protocols: list[str] = []
    header_val = websocket.headers.get("sec-websocket-protocol", "")
    if header_val:
        protocols.extend(p.strip() for p in header_val.split(","))
    protocols.extend(websocket.scope.get("subprotocols") or [])
    for proto in protocols:
        if proto.startswith("vinylsync-auth."):
            return proto, proto.removeprefix("vinylsync-auth.").strip()
        if proto.startswith("bearer."):
            return proto, proto.removeprefix("bearer.").strip()
        if proto == "vinylsync-auth":
            return proto, None
    return None, None


def extract_ws_subprotocol_token(websocket: WebSocket) -> str | None:
    """Extract auth token from Sec-WebSocket-Protocol headers if present."""
    return extract_ws_subprotocol(websocket)[1]
