"""Cross-provider aggregator for long-lived API tokens.

Merges three disjoint sources of API credentials into one uniform
list for the session-visibility UI:

* Controller bearer tokens (``core.auth.api_token_store``).
* Jellyfin API keys (via ``JellyfinApiProvider``).
* \\*arr API keys (Sonarr/Radarr/Lidarr/Readarr/Prowlarr).

Each row is tagged with its source ``provider`` so the dashboard
renders a single table and revoke-by-id routes back to the right
backend.

Design invariants
-----------------
* Never raises on provider failure — a broken backend contributes
  zero rows, logged at ``debug``.
* ``APITokenRecord`` carries metadata only. Providers already strip
  plaintext; the ratchet test in
  ``tests/unit/test_api_token_aggregator.py`` freezes the field set.
* ``revoke`` is admin-only and always audits
  (``audit_actions.SESSION_REVOKED``) — attempts against unknown ids
  are worth recording.
* The controller's token store is consumed through a local
  ``Protocol`` rather than a hard import, keeping this module
  decoupled from the concrete ``ApiTokenStore``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from media_stack.core.auth.authz import Actor, requires_admin
from media_stack.core.auth.users import audit_actions
from media_stack.core.auth.users.visibility_protocols import (
    APIToken,
    APITokenProvider,
)

_log = logging.getLogger("media_stack.security.api_token_aggregator")

CONTROLLER_PROVIDER = "controller"


# ---------------------------------------------------------------------------
# Audit sink protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class _AuditSink(Protocol):
    """The slice of ``AuditLog`` we actually use.

    Kept private + structural so the aggregator doesn't hard-depend
    on the concrete ``AuditLog`` class and tests can pass a stub.
    """

    def append(
        self,
        actor: str,
        action: str,
        target: str,
        result: str = ...,
        ip: str = ...,
        user_agent: str = ...,
        detail: dict[str, Any] | None = ...,
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Controller token store protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ControllerTokenStoreProtocol(Protocol):
    """Minimal surface the aggregator needs from the controller's own
    token store.

    Declared locally so this module doesn't hard-depend on
    ``core.auth.api_token_store.ApiTokenStore`` — production wiring
    passes a thin adapter that exposes only what the aggregator uses.

    Implementations MUST:
      * Return an empty list (not raise) for unknown users.
      * ``revoke_token`` returns ``True`` on a state change,
        ``False`` for unknown / already-revoked ids (idempotent).
    """

    def list_by_user(self, username: str) -> list[APIToken]: ...

    def revoke_token(self, token_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# APITokenRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class APITokenRecord:
    """Provider-tagged view of an API token, UI-safe.

    Mirrors ``visibility_protocols.APIToken`` with an added
    ``provider`` discriminant so the dashboard knows which backend
    the row came from (and which backend to call on revoke).

    SECURITY INVARIANT: this struct MUST NOT gain a field that
    could carry a token secret. The ratchet test
    ``test_apitokenrecord_has_no_secret_field`` freezes the field
    set — any change to the shape needs a security review.
    """

    provider: str
    token_id: str
    name: str
    created_at: str
    last_used_at: str
    scopes: tuple[str, ...] = ()
    created_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "token_id": self.token_id,
            "name": self.name,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "scopes": list(self.scopes),
            "created_by": self.created_by,
        }

    @classmethod
    def from_api_token(cls, provider: str, tok: APIToken) -> "APITokenRecord":
        return cls(
            provider=provider,
            token_id=tok.token_id,
            name=tok.name,
            created_at=tok.created_at,
            last_used_at=tok.last_used_at,
            scopes=tuple(tok.scopes),
            created_by=tok.created_by,
        )


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class APITokenAggregator:
    """Fan-out aggregator for cross-provider API-token visibility.

    Parameters
    ----------
    controller_token_store:
        Adapter over the controller's own ``ApiTokenStore``. May be
        ``None`` in deployments that don't expose controller tokens
        (e.g. reduced footprint installations).
    providers:
        Iterable of ``APITokenProvider`` implementations (typically
        ``JellyfinApiProvider`` + one per configured \\*arr). Each
        entry is validated against the protocol at construction.
    audit_log:
        Optional ``AuditLog``-shaped sink. ``revoke`` writes one
        entry per attempt; when absent the call still runs but
        leaves no forensics trail — useful for tests but not for
        production wiring.
    """

    def __init__(
        self,
        *,
        controller_token_store: ControllerTokenStoreProtocol | None = None,
        providers: list[APITokenProvider] | None = None,
        audit_log: _AuditSink | None = None,
    ) -> None:
        providers = list(providers or [])
        for p in providers:
            if not isinstance(p, APITokenProvider):
                raise TypeError(
                    f"provider {p!r} does not satisfy APITokenProvider "
                    "(needs name, list_api_tokens, revoke_api_token)"
                )
        self._controller = controller_token_store
        self._providers: dict[str, APITokenProvider] = {p.name: p for p in providers}
        self._audit = audit_log

    # -- sort helpers ----------------------------------------------------

    def _neg_key(self, s: str) -> tuple[int, str]:
        """Return a sort key that orders ``s`` descending when combined with
        an ascending ``sort``. ISO-8601 timestamps (``YYYY-MM-DDTHH:MM:SSZ``)
        sort lexicographically in chronological order, so negating is as
        simple as inverting each codepoint via a reverse-order key.

        Using a tuple ``(-len, inverted)`` keeps empty strings at the end
        (unknown created_at shouldn't outrank a known one).
        """
        if not s:
            # Unknown created_at sinks to the bottom of its provider group.
            return (1, "")
        # 0x10FFFF is the max code point; subtracting produces the
        # lexicographic inverse so ascending sort => descending order.
        inverted = "".join(chr(0x10FFFF - ord(c)) for c in s)
        return (0, inverted)

    # -- listing ---------------------------------------------------------

    def list_for_user(
        self,
        *,
        username: str,
        external_id_for_provider: dict[str, str] | None = None,
    ) -> list[APITokenRecord]:
        """Return every known API-token record for ``username``.

        Controller tokens are looked up by controller ``username``.
        Each external provider is queried via its own external id
        (resolved from ``external_id_for_provider``); providers not
        in the map contribute zero rows. A provider that raises is
        swallowed + logged at debug — a broken backend must not
        take down the aggregate view.

        Ordering: provider name ascending, then ``created_at``
        descending (newest first within a provider).
        """
        external_id_for_provider = dict(external_id_for_provider or {})
        records: list[APITokenRecord] = []

        # Controller
        if self._controller is not None and username:
            try:
                controller_tokens = self._controller.list_by_user(username)
            except Exception as exc:  # noqa: BLE001 — contract: never raise
                _log.debug(
                    "controller_token_store.list_by_user(%r) failed: %s",
                    username, exc,
                )
                controller_tokens = []
            for tok in controller_tokens:
                records.append(
                    APITokenRecord.from_api_token(CONTROLLER_PROVIDER, tok)
                )

        # External providers
        for name, provider in self._providers.items():
            ext_id = external_id_for_provider.get(name, "")
            if not ext_id:
                # No linkage for this user on this provider — contribute
                # nothing. Not an error; *arrs may be anonymous, a user
                # may not be enrolled in Jellyfin, etc.
                continue
            try:
                tokens = provider.list_api_tokens(ext_id)
            except Exception as exc:  # noqa: BLE001 — contract: never raise
                _log.debug(
                    "provider %s list_api_tokens(%r) failed: %s",
                    name, ext_id, exc,
                )
                continue
            for tok in tokens:
                records.append(APITokenRecord.from_api_token(name, tok))

        records.sort(key=lambda r: (r.provider, self._neg_key(r.created_at)))
        return records

    # -- revocation ------------------------------------------------------

    @requires_admin
    def revoke(
        self,
        *,
        provider: str,
        token_id: str,
        external_id: str = "",
        actor: Actor,
    ) -> bool:
        """Revoke ``token_id`` on ``provider``. Admin only.

        Returns ``True`` if the backend reported a state change,
        ``False`` otherwise (unknown id, already revoked, or
        backend failure). Either way an audit entry is written so
        revocation attempts are traceable.

        The external provider revoke API is idempotent (by protocol
        contract) and returns no value; we conservatively report
        ``True`` on success and ``False`` when the provider raises.
        """
        result_ok = False
        detail: dict[str, Any] = {
            "provider": provider,
            "token_id": token_id,
        }
        if external_id:
            # External ids may be useful forensically (e.g. "did the
            # admin aim at the right Jellyfin account?") but are not
            # secrets. Include only when non-empty to keep the audit
            # row small.
            detail["external_id"] = external_id

        if provider == CONTROLLER_PROVIDER:
            if self._controller is None:
                detail["reason"] = "controller_store_not_configured"
            else:
                try:
                    result_ok = bool(self._controller.revoke_token(token_id))
                except Exception as exc:  # noqa: BLE001
                    _log.debug(
                        "controller revoke_token(%r) raised: %s",
                        token_id, exc,
                    )
                    detail["reason"] = "exception"
        else:
            impl = self._providers.get(provider)
            if impl is None:
                detail["reason"] = "unknown_provider"
            elif not external_id:
                # External providers scope tokens by user; without
                # the external id we can't address the call.
                detail["reason"] = "missing_external_id"
            else:
                try:
                    impl.revoke_api_token(external_id, token_id)
                    result_ok = True
                except Exception as exc:  # noqa: BLE001
                    _log.debug(
                        "provider %s revoke_api_token(%r,%r) raised: %s",
                        provider, external_id, token_id, exc,
                    )
                    detail["reason"] = "exception"

        self._audit_revoke(
            actor=actor,
            token_id=token_id,
            success=result_ok,
            detail=detail,
        )
        return result_ok

    # -- internals -------------------------------------------------------

    def _audit_revoke(
        self,
        *,
        actor: Actor,
        token_id: str,
        success: bool,
        detail: dict[str, Any],
    ) -> None:
        if self._audit is None:
            return
        try:
            self._audit.append(
                actor=actor.audit_label,
                action=audit_actions.SESSION_REVOKED,
                target=token_id,
                result="ok" if success else "not_found",
                ip=actor.client_ip,
                user_agent=actor.user_agent,
                detail=detail,
            )
        except Exception as exc:  # noqa: BLE001
            # The audit sink failing must not poison the user-facing
            # operation. Surface at debug; ops dashboards already
            # monitor audit-log write health separately.
            _log.debug("audit append failed for revoke: %s", exc)


# ---------------------------------------------------------------------------
# Module-level instance + aliases
# ---------------------------------------------------------------------------

# Default singleton used purely so the legacy module-level ``_neg_key``
# alias keeps working. Production callers always go through an
# explicitly-constructed ``APITokenAggregator`` with real providers.
_INSTANCE = APITokenAggregator()
_neg_key = _INSTANCE._neg_key


__all__ = [
    "APITokenAggregator",
    "APITokenRecord",
    "CONTROLLER_PROVIDER",
    "ControllerTokenStoreProtocol",
]
