"""Application configuration loading."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _find_root_dir() -> Path:
    # 1. In-tree development (Path(__file__).resolve().parents[2]) if config or pyproject exists
    dev_root = Path(__file__).resolve().parents[2]
    if (
        (dev_root / "config.yaml").is_file()
        or (dev_root / "config.example.yaml").is_file()
        or (dev_root / "pyproject.toml").is_file()
    ):
        return dev_root
    # 2. Current working directory if config exists
    if (Path.cwd() / "config.yaml").is_file() or (Path.cwd() / "config.example.yaml").is_file():
        return Path.cwd()
    # 3. Look upward from current working directory
    for parent in Path.cwd().parents:
        if (parent / "config.yaml").is_file() or (parent / "config.example.yaml").is_file():
            return parent
    return dev_root


ROOT_DIR = _find_root_dir()


def _resolve_default_config_path() -> Path:
    if (ROOT_DIR / "config.yaml").is_file():
        return ROOT_DIR / "config.yaml"
    if (Path.cwd() / "config.yaml").is_file():
        return Path.cwd() / "config.yaml"
    if (ROOT_DIR / "config.example.yaml").is_file():
        return ROOT_DIR / "config.example.yaml"
    if (Path.cwd() / "config.example.yaml").is_file():
        return Path.cwd() / "config.example.yaml"
    return ROOT_DIR / "config.yaml"


DEFAULT_CONFIG_PATH = _resolve_default_config_path()


def load_dotenv_if_present() -> None:
    """Load environment variables from a local .env file when present."""
    if (ROOT_DIR / ".env").is_file():
        load_dotenv(ROOT_DIR / ".env")
    elif (Path.cwd() / ".env").is_file():
        load_dotenv(Path.cwd() / ".env")
    else:
        load_dotenv()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_config(config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load YAML configuration and apply environment overrides."""
    load_dotenv_if_present()

    path = (
        Path(config_path)
        if config_path
        else Path(os.getenv("VINYL_LYRICS_CONFIG", DEFAULT_CONFIG_PATH))
    )
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()

    if not path.is_file() and (ROOT_DIR / "config.example.yaml").is_file():
        path = ROOT_DIR / "config.example.yaml"

    with path.open("r", encoding="utf-8") as handle:
        config: dict[str, Any] = yaml.safe_load(handle) or {}

    env_overrides = {
        "recognition": {
            "acrcloud": {
                "host": os.getenv("ACR_HOST"),
                "access_key": os.getenv("ACR_ACCESS_KEY"),
                "access_secret": os.getenv("ACR_ACCESS_SECRET"),
            },
            "audd": {"api_token": os.getenv("AUDD_API_TOKEN")},
        }
    }

    cleaned = {
        key: value for key, value in env_overrides["recognition"].items() if any(value.values())
    }
    if cleaned:
        config.setdefault("recognition", {})
        config["recognition"] = _deep_merge(config["recognition"], cleaned)

    return validate_config(config)


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate configuration values and relationships."""
    audio = config.get("audio", {})
    if "sample_rate" in audio and float(audio["sample_rate"]) <= 0:
        raise ValueError("audio.sample_rate must be positive.")
    if "buffer_seconds" in audio and float(audio["buffer_seconds"]) <= 0:
        raise ValueError("audio.buffer_seconds must be positive.")

    recognition = config.get("recognition", {})
    positive_recognition_keys = (
        "interval_seconds",
        "min_signal_seconds",
        "track_gap_seconds",
        "idle_timeout_seconds",
        "min_sample_seconds",
        "break_max_seconds",
        "end_margin_seconds",
        "timeout_seconds",
        "candidate_ttl_seconds",
    )
    for key in positive_recognition_keys:
        if key in recognition and float(recognition[key]) <= 0:
            raise ValueError(f"recognition.{key} must be positive.")

    track_gap = float(recognition.get("track_gap_seconds", 1.5))
    break_max = float(recognition.get("break_max_seconds", 10.0))
    if break_max < track_gap:
        raise ValueError("recognition.break_max_seconds must be >= recognition.track_gap_seconds.")

    return config


__all__ = ["DEFAULT_CONFIG_PATH", "load_config", "validate_config"]
