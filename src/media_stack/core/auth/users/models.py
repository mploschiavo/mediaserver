"""Data models for user management."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class UserState(str, Enum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


@dataclass
class User:
    id: str
    email: str
    username: str
    display_name: str
    state: UserState
    role_slug: str
    created_at: str
    updated_at: str
    last_login_at: str = ""
    provider_refs: dict[str, str] = field(default_factory=dict)
    password_history: list[str] = field(default_factory=list)

    def to_dict(self, include_sensitive: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "display_name": self.display_name,
            "state": self.state.value,
            "role_slug": self.role_slug,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_login_at": self.last_login_at,
            "provider_refs": dict(self.provider_refs),
        }
        if include_sensitive:
            out["password_history"] = list(self.password_history)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "User":
        return cls(
            id=str(data["id"]),
            email=str(data["email"]),
            username=str(data["username"]),
            display_name=str(data.get("display_name", "")),
            state=UserState(data.get("state", "active")),
            role_slug=str(data.get("role_slug", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            last_login_at=str(data.get("last_login_at", "")),
            provider_refs=dict(data.get("provider_refs", {})),
            password_history=list(data.get("password_history", [])),
        )


@dataclass
class Role:
    slug: str
    name: str
    description: str = ""
    sso_groups: list[str] = field(default_factory=list)
    # provider_payloads is a map of provider-id → arbitrary payload for that
    # provider's update_user/create_user call. Keeping it provider-keyed
    # means the core model never mentions a specific backend.
    provider_payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Service-admin propagation: when true, password changes on users with
    # this role also update every ServiceAdminProvider (single-admin
    # services that don't have user accounts — qBit, Arrs, Bazarr, etc).
    # Implies controller-UI access: the same credential authenticates
    # against the controller's basic-auth.
    propagate_to_service_admins: bool = False
    # When true, users with this role MUST have 2FA enrolled in Authelia
    # before they can authenticate to the controller UI. Enforced by
    # BasicAuthVerifier when it sees the flag on the matched role.
    require_2fa: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "sso_groups": list(self.sso_groups),
            "provider_payloads": {
                k: dict(v) for k, v in self.provider_payloads.items()
            },
            "propagate_to_service_admins": self.propagate_to_service_admins,
            "require_2fa": self.require_2fa,
        }

    @classmethod
    def from_dict(cls, slug: str, data: dict[str, Any]) -> "Role":
        return cls(
            slug=slug,
            name=str(data.get("name", slug)),
            description=str(data.get("description", "")),
            sso_groups=list(data.get("sso_groups", [])),
            provider_payloads={
                k: dict(v or {})
                for k, v in (data.get("provider_payloads") or {}).items()
                if isinstance(k, str)
            },
            propagate_to_service_admins=bool(
                data.get("propagate_to_service_admins", False),
            ),
            require_2fa=bool(data.get("require_2fa", False)),
        )


@dataclass
class Invite:
    id: str
    email: str
    role_slug: str
    created_by: str
    created_at: str
    expires_at: str
    token_hash: str
    accepted_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "role_slug": self.role_slug,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "token_hash": self.token_hash,
            "accepted_at": self.accepted_at,
        }


@dataclass
class AuditEntry:
    timestamp: str
    actor: str
    action: str
    target: str
    result: str
    ip: str = ""
    user_agent: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = ""
    hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "result": self.result,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "detail": dict(self.detail),
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }
