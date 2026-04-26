"""Optional user-provider protocols for session visibility.

The base ``UserProvider`` protocol in ``provider.py`` covers the
CRUD surface every backend must implement. Session visibility and
security reporting need a richer view — who is logged in, from
where, on what device, with what MFA, holding which API tokens.

Not every backend can answer every question. Rather than bolt
everything onto ``UserProvider`` (and force every impl to no-op on
things it can't do), we split the optional surface into small
role-protocols that providers mix in where they have something real
to say.

Protocols
---------
- ``SessionAdminProvider``   — list + revoke live sessions.
- ``AccountStateProvider``   — disable / enable a user account.
- ``MFAStateProvider``       — read enrolled 2FA methods.
- ``APITokenProvider``       — list + revoke long-lived API tokens.

A provider may implement any subset. Callers that need, say,
session listing can use ``isinstance(p, SessionAdminProvider)`` or
a runtime ``hasattr`` probe (see ``services.security.session_aggregator``).

Dataclasses
-----------
All value objects returned by these protocols are frozen
dataclasses with a ``to_dict()`` for JSON serialization. No object
contains credentials — tokens expose metadata only. The controller
never sees a plaintext API token after creation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from media_stack.domain.auth.users.provider import ExternalSession


# ---------------------------------------------------------------------------
# MFAState
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MFAState:
    """Multi-factor authentication status for a user at a provider.

    ``enrolled_methods`` is ordered by the provider's preferred
    method first (e.g. ("webauthn", "totp") if the user has both).
    An empty tuple means no MFA enrolled.

    ``required`` indicates whether the role/policy forces MFA — a
    user may be ``enrolled=False, required=True`` during onboarding
    if they haven't completed enrollment yet.
    """

    enrolled: bool
    enrolled_methods: tuple[str, ...] = ()
    last_used_method: str = ""
    last_used_at: str = ""
    required: bool = False

    @classmethod
    def none(cls) -> "MFAState":
        return cls(enrolled=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enrolled": self.enrolled,
            "enrolled_methods": list(self.enrolled_methods),
            "last_used_method": self.last_used_method,
            "last_used_at": self.last_used_at,
            "required": self.required,
        }


# ---------------------------------------------------------------------------
# APIToken
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class APIToken:
    """Metadata for a long-lived API token (never the secret itself).

    ``token_id`` is whatever opaque identifier the backend uses to
    refer to the token (Jellyfin's AccessToken "AppName:DeviceId",
    controller's token family id, etc.). It must be stable so that
    revoke-by-id works from the UI.

    ``scopes`` is provider-specific — Jellyfin has no notion, *arrs
    have none either, controller tokens carry a scope list.
    ``created_by`` carries whatever attribution the backend knows,
    typically the account name that minted the token.

    The full token string must NEVER appear in this struct. The
    controller only ever surfaces token METADATA after the one-time
    creation flow returns the secret to the admin.
    """

    token_id: str
    name: str = ""
    created_at: str = ""
    last_used_at: str = ""
    scopes: tuple[str, ...] = ()
    created_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "name": self.name,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "scopes": list(self.scopes),
            "created_by": self.created_by,
        }


# ---------------------------------------------------------------------------
# Protocol surfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionAdminProvider(Protocol):
    """Providers that can enumerate + revoke live sessions.

    Implementors MUST:
      * Return an empty list (not raise) when the user has no
        sessions or when the backend is transiently unreachable.
      * Make ``revoke_sessions`` idempotent.
      * Tolerate unknown ``session_id`` in ``revoke_session`` —
        treat it as already-gone.

    Jellyfin and Authelia both implement this protocol; the Null
    provider implements it trivially.
    """

    name: str

    def list_sessions(self, external_id: str) -> list[ExternalSession]: ...

    def revoke_sessions(self, external_id: str) -> None: ...

    def revoke_session(self, external_id: str, session_id: str) -> None: ...


@runtime_checkable
class AccountStateProvider(Protocol):
    """Providers that can disable / enable an account.

    Disabling MUST:
      * Prevent new sign-ins.
      * Be idempotent.

    Disabling SHOULD invalidate live sessions where the provider
    supports it (most do — Authelia flips ``disabled: true`` and
    its auth middleware rejects on next request; Jellyfin sets
    ``IsDisabled=true`` and live clients get kicked on next call).

    ``is_disabled`` must return the current persisted state (not a
    cached one) so the UI never lies to the admin after a manual
    backend edit.
    """

    name: str

    def disable_user(self, external_id: str) -> None: ...

    def enable_user(self, external_id: str) -> None: ...

    def is_disabled(self, external_id: str) -> bool: ...


@runtime_checkable
class MFAStateProvider(Protocol):
    """Providers that can report a user's 2FA enrollment status.

    Implementors return ``MFAState.none()`` for users with no MFA
    rather than raising. Transient backend errors also return
    ``MFAState.none()`` — the UI prefers a conservative "unknown"
    to a hard error, and paints an ambiguity indicator separately
    (see ``ProviderHealth``).
    """

    name: str

    def mfa_state(self, external_id: str) -> MFAState: ...


@runtime_checkable
class APITokenProvider(Protocol):
    """Providers that issue long-lived API tokens per user.

    Implementors return ``[]`` for users without any tokens or when
    the backend is unreachable. ``revoke_api_token`` is idempotent:
    an unknown ``token_id`` is treated as already-gone.

    Token SECRETS must never flow through this protocol — only
    metadata. Minting new tokens is out of scope here; the
    controller has its own token-minting endpoint, and other
    services (Jellyfin, *arrs) mint through their own UIs.
    """

    name: str

    def list_api_tokens(self, external_id: str) -> list[APIToken]: ...

    def revoke_api_token(self, external_id: str, token_id: str) -> None: ...


__all__ = [
    "APIToken",
    "APITokenProvider",
    "AccountStateProvider",
    "MFAState",
    "MFAStateProvider",
    "SessionAdminProvider",
]
