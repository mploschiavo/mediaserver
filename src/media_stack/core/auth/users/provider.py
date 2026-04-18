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


@dataclass
class ExternalSession:
    session_id: str
    device: str = ""
    client: str = ""
    last_activity: str = ""
    ip: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "device": self.device,
            "client": self.client,
            "last_activity": self.last_activity,
            "ip": self.ip,
        }


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

    def revoke_sessions(self, external_id: str) -> None:
        """Invalidate the user's active sessions (best-effort).

        Optional: providers that don't support session revocation may
        no-op. Called by UserService on delete and (optionally) on role
        changes that should force re-auth.
        """
        ...

    def list_sessions(self, external_id: str) -> list[ExternalSession]:
        """Return the user's active sessions (best-effort, may be empty)."""
        ...

    def last_activity(self, external_id: str) -> str:
        """Return an ISO timestamp of the user's last activity, or ''."""
        ...
