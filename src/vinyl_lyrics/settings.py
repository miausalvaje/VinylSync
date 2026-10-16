"""Persistent user settings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir
from pydantic import BaseModel, Field


class UserSettings(BaseModel):
    """Validated settings that can be changed while the app is running."""

    source_id: str | None = None
    gain_db: float = Field(default=0.0, ge=-30.0, le=30.0)
    silence_enter_dbfs: float = Field(default=-48.0, ge=-100.0, le=0.0)
    silence_exit_dbfs: float = Field(default=-54.0, ge=-100.0, le=0.0)
    font_size: float = Field(default=1.0, ge=0.5, le=3.0)
    manual_offset_seconds: float = Field(default=0.0, ge=-30.0, le=30.0)


class SettingsStore:
    """Read and write settings.json in the platform user config directory."""

    def __init__(self, path: str | Path | None = None, user_key: str | None = None) -> None:
        if path is not None:
            self.path = Path(path)
        elif user_key:
            # Hash rather than use usernames directly to avoid unsafe path components.
            safe_key = hashlib.sha256(user_key.casefold().encode("utf-8")).hexdigest()[:24]
            self.path = Path(user_config_dir("VinylSync")) / "users" / safe_key / "settings.json"
        else:
            self.path = Path(user_config_dir("VinylSync")) / "settings.json"

    def load(self, defaults: UserSettings | None = None) -> UserSettings:
        result = defaults or UserSettings()
        if not self.path.exists():
            return result
        with self.path.open("r", encoding="utf-8") as handle:
            payload: dict[str, Any] = json.load(handle)
        return UserSettings.model_validate({**result.model_dump(), **payload})

    def save(self, settings: UserSettings) -> UserSettings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(settings.model_dump(), handle, indent=2)
        return settings
