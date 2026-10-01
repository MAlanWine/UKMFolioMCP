"""Configuration loading for the UKMFolio MCP server.

Reuses the same ``config.json`` schema as UKMFolioPuller, but only the
login-related fields are required here (Telegram fields are ignored if present).

Resolution order for the config file path:
1. ``--config <path>`` CLI argument (passed in via ``load_config(path=...)``)
2. ``UKMFOLIO_CONFIG`` environment variable
3. ``config.json`` next to the project root (one level above this package)
4. ``config.json`` in the current working directory
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_DEFAULT_SEARCH_PATHS = (
    _PROJECT_ROOT / "config.json",
    Path.cwd() / "config.json",
)

DEFAULT_TIMEZONE = "Asia/Kuala_Lumpur"


@dataclass
class Config:
    """Validated server configuration."""

    username: str
    password: str
    base_url: str = "https://ukmfoliov2.ukm.my"
    sso_url: str = "https://sso.ukm.my"
    timezone: str = DEFAULT_TIMEZONE
    # HTTP transport defaults (overridable via CLI)
    host: str = "127.0.0.1"
    port: int = 8000

    def as_login_dict(self) -> dict:
        """Shape expected by the auth module."""
        return {
            "username": self.username,
            "password": self.password,
            "base_url": self.base_url.rstrip("/"),
            "sso_url": self.sso_url.rstrip("/"),
        }


def _resolve_path(path: str | os.PathLike | None) -> Path:
    if path:
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {p}")
        return p

    env_path = os.environ.get("UKMFOLIO_CONFIG")
    if env_path:
        p = Path(env_path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(
                f"UKMFOLIO_CONFIG points to a missing file: {p}"
            )
        return p

    for candidate in _DEFAULT_SEARCH_PATHS:
        if candidate.is_file():
            return candidate

    searched = ", ".join(str(p) for p in _DEFAULT_SEARCH_PATHS)
    raise FileNotFoundError(
        "No config.json found. Pass --config <path>, set UKMFOLIO_CONFIG, "
        f"or place config.json at one of: {searched}"
    )


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load and validate configuration from ``config.json``.

    Environment variables ``UKMFOLIO_USERNAME`` / ``UKMFOLIO_PASSWORD`` override
    the corresponding file fields when set (handy for server deployments that
    keep the file free of plaintext credentials).
    """
    config_path = _resolve_path(path)
    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    username = os.environ.get("UKMFOLIO_USERNAME") or raw.get("username")
    password = os.environ.get("UKMFOLIO_PASSWORD") or raw.get("password")

    missing = [
        name
        for name, value in (("username", username), ("password", password))
        if not value
    ]
    if missing:
        raise ValueError(
            f"Missing required field(s) in {config_path}: {', '.join(missing)} "
            "(or set UKMFOLIO_USERNAME / UKMFOLIO_PASSWORD)"
        )

    return Config(
        username=username,
        password=password,
        base_url=raw.get("base_url", "https://ukmfoliov2.ukm.my"),
        sso_url=raw.get("sso_url", "https://sso.ukm.my"),
        timezone=raw.get("timezone", DEFAULT_TIMEZONE),
        host=raw.get("host", "127.0.0.1"),
        port=int(raw.get("port", 8000)),
    )
