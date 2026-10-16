"""Stable paths for persistent VinylSync data."""

from __future__ import annotations

import os
from pathlib import Path


def get_data_dir() -> Path:
    """Return the configured persistent data directory or the project data directory."""
    configured = os.getenv("VINYLSYNC_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "data"


def resolve_data_path(path: str | Path) -> Path:
    """Resolve relative persistent-data paths against the central data directory."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    parts = candidate.parts
    # Existing defaults use paths such as data/users.sqlite3; avoid a redundant data/data nesting.
    if parts and parts[0] == "data":
        candidate = Path(*parts[1:]) if len(parts) > 1 else Path()
    return get_data_dir() / candidate


__all__ = ["get_data_dir", "resolve_data_path"]
