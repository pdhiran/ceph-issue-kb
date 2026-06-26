"""Load and validate connectors.yaml configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AuthConfig:
    method: str = "none"
    username_env: str = ""
    token_env: str = ""
    key_env: str = ""
    cookie_env: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> AuthConfig:
        if data is None:
            return cls(method="none")
        return cls(
            method=data.get("method", "none"),
            username_env=data.get("username_env", ""),
            token_env=data.get("token_env", ""),
            key_env=data.get("key_env", ""),
            cookie_env=data.get("cookie_env", ""),
        )


@dataclass
class ConnectorConfig:
    name: str = ""
    connector_type: str = ""
    enabled: bool = True
    base_url: str = ""
    auth: AuthConfig = field(default_factory=AuthConfig)
    rate_limit: int = 10
    since: str = "2024-01-01"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> ConnectorConfig:
        auth_data = data.get("auth")
        known_keys = {"type", "enabled", "base_url", "auth", "rate_limit", "since"}
        extra = {k: v for k, v in data.items() if k not in known_keys}
        return cls(
            name=name,
            connector_type=data.get("type", ""),
            enabled=data.get("enabled", True),
            base_url=data.get("base_url", "").rstrip("/"),
            auth=AuthConfig.from_dict(auth_data),
            rate_limit=data.get("rate_limit", 10),
            since=data.get("since", "2024-01-01"),
            extra=extra,
        )


@dataclass
class Config:
    connectors: dict[str, ConnectorConfig] = field(default_factory=dict)

    @property
    def enabled_connectors(self) -> dict[str, ConnectorConfig]:
        return {k: v for k, v in self.connectors.items() if v.enabled}


def load_config(path: str | Path) -> Config:
    """Load connectors.yaml and return a validated Config."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    raw = yaml.safe_load(p.read_text())
    if "connectors" not in raw:
        raise ValueError(f"Invalid config: missing 'connectors' key in {p}")
    connectors = {
        name: ConnectorConfig.from_dict(name, data)
        for name, data in raw["connectors"].items()
    }
    return Config(connectors=connectors)
