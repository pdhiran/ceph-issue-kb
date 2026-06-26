"""Resolve credentials from environment variables.

Credentials are never stored in code or config files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ceph_issue_kb.config import AuthConfig


class AuthError(Exception):
    """Raised when required credentials are missing."""


@dataclass
class Credentials:
    """Resolved credential set ready for use by a connector."""

    method: str = "none"
    username: str = ""
    token: str = ""
    api_key: str = ""
    cookie: str = ""


class AuthProvider:
    """Resolves credentials from AuthConfig by reading environment variables."""

    def resolve(self, auth_config: AuthConfig) -> Credentials:
        if auth_config.method == "none":
            return Credentials(method="none")
        if auth_config.method == "api_token":
            return Credentials(
                method="api_token",
                username=self._env(auth_config.username_env, "username"),
                token=self._env(auth_config.token_env, "API token"),
            )
        if auth_config.method == "api_key":
            return Credentials(
                method="api_key",
                api_key=self._env(auth_config.key_env, "API key"),
            )
        if auth_config.method == "cookie":
            return Credentials(
                method="cookie",
                cookie=self._env(auth_config.cookie_env, "cookie"),
            )
        raise AuthError(f"Unknown auth method: {auth_config.method}")

    @staticmethod
    def _env(var_name: str, label: str) -> str:
        if not var_name:
            raise AuthError(f"No environment variable configured for {label}")
        value = os.environ.get(var_name, "")
        if not value:
            raise AuthError(
                f"Environment variable {var_name} ({label}) is not set or empty"
            )
        return value
