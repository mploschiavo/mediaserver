"""Null user provider — safe no-op implementation of every protocol.

Used during init-only bootstrap (before an auth backend is
configured) and as a test double for code that needs a provider
shape but doesn't exercise it.

Semantics:
  * Read methods return empty results (never raise).
  * Destructive methods are idempotent no-ops (also never raise).
  * State-creating methods raise ``NullProviderError`` — a test that
    tries to actually create a user through Null is almost
    certainly a bug.

The class is deliberately concrete (not abstract) and carries a
configurable ``name`` so audit entries show which null-path was
taken in a deployment that has multiple null slots.
"""

from __future__ import annotations

from typing import Any

from media_stack.core.auth.users.ip_deny import IPDeny
from media_stack.core.auth.users.provider import (
    ExternalSession,
    ExternalUser,
    ProviderCapabilities,
    ProviderHealth,
)
from media_stack.core.auth.users.visibility_protocols import (
    APIToken,
    MFAState,
)


class NullProviderError(RuntimeError):
    """Raised when a caller tries to mutate state via the null
    provider. Pure no-ops stay silent; writes blow up loudly so the
    mistake is visible."""


class NullProvider:
    """Implements UserProvider + every optional protocol as no-op."""

    capabilities = ProviderCapabilities()

    def __init__(self, name: str = "null") -> None:
        if not name:
            raise ValueError("NullProvider requires a non-empty name")
        self.name = name

    # ---- UserProvider ----------------------------------------------------

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=False, detail="null provider")

    def list_users(self) -> list[ExternalUser]:
        return []

    def create_user(
        self,
        *,
        username: str,
        email: str,
        display_name: str,
        password: str,
        groups: list[str],
        policy: dict[str, Any] | None = None,
    ) -> ExternalUser:
        del username, email, display_name, password, groups, policy
        raise NullProviderError(
            "create_user is not supported by the null provider",
        )

    def update_user(
        self,
        external_id: str,
        *,
        display_name: str = "",
        email: str = "",
        groups: list[str] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> ExternalUser:
        del external_id, display_name, email, groups, policy
        raise NullProviderError(
            "update_user is not supported by the null provider",
        )

    def delete_user(self, external_id: str) -> None:
        # Idempotent no-op: if nothing is there, nothing to delete.
        del external_id

    def set_password(self, external_id: str, password: str) -> None:
        del external_id, password
        raise NullProviderError(
            "set_password is not supported by the null provider",
        )

    def last_activity(self, external_id: str) -> str:
        del external_id
        return ""

    # ---- SessionAdminProvider --------------------------------------------

    def list_sessions(self, external_id: str) -> list[ExternalSession]:
        del external_id
        return []

    def revoke_sessions(self, external_id: str) -> None:
        del external_id

    def revoke_session(self, external_id: str, session_id: str) -> None:
        del external_id, session_id

    # ---- AccountStateProvider --------------------------------------------

    def disable_user(self, external_id: str) -> None:
        del external_id

    def enable_user(self, external_id: str) -> None:
        del external_id

    def is_disabled(self, external_id: str) -> bool:
        del external_id
        return False

    # ---- MFAStateProvider ------------------------------------------------

    def mfa_state(self, external_id: str) -> MFAState:
        del external_id
        return MFAState.none()

    # ---- APITokenProvider ------------------------------------------------

    def list_api_tokens(self, external_id: str) -> list[APIToken]:
        del external_id
        return []

    def revoke_api_token(self, external_id: str, token_id: str) -> None:
        del external_id, token_id

    # ---- IPDenyProvider --------------------------------------------------

    def list_ip_denies(self) -> list[IPDeny]:
        return []

    def add_ip_deny(self, rule: IPDeny) -> None:
        del rule

    def remove_ip_deny(self, cidr: str) -> None:
        del cidr


__all__ = ["NullProvider", "NullProviderError"]
