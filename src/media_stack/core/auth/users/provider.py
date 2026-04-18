"""UserProvider protocol + capability flags + ExternalUser.

Provider-neutral. Concrete implementations live under
``services/apps/<service>/user_provider.py`` (one per backend). Anything
importable from this module MUST stay generic — no service names here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ProviderCapabilities:
    source_of_truth: bool = False
    supports_groups: bool = False
    supports_password: bool = False
    supports_policy: bool = False
    auto_provisions_on_login: bool = False


@dataclass
class ExternalUser:
    external_id: str
    username: str
    email: str = ""
    groups: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderHealth:
    ok: bool
    detail: str = ""


class UserProvider(Protocol):
    """Minimal surface every user-management backend must implement."""

    name: str
    capabilities: ProviderCapabilities

    def health_check(self) -> ProviderHealth: ...
    def list_users(self) -> list[ExternalUser]: ...
    def create_user(self, *, username: str, email: str, display_name: str,
                    password: str, groups: list[str],
                    policy: dict[str, Any] | None = None) -> ExternalUser: ...
    def update_user(self, external_id: str, *, display_name: str = "",
                    email: str = "",
                    groups: list[str] | None = None,
                    policy: dict[str, Any] | None = None) -> ExternalUser: ...
    def delete_user(self, external_id: str) -> None: ...
    def set_password(self, external_id: str, password: str) -> None: ...
