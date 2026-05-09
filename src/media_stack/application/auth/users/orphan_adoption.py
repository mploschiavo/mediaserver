"""Orphan adoption for the user-create path.

When a dashboard "Create user" request names a username or email that
already exists in the source-of-truth provider (Authelia) — but NOT in
the controller's central users.json store — the create path used to
hard-fail with ``source-of-truth failed: user 'X' already exists``.
The operator was left with no in-UI escape: the user wasn't in the
store (so it wasn't visible), and every attempt to create re-hit the
provider and re-failed.

This helper finds the orphan proactively BEFORE the central-store row
is written, and lets the caller re-route through an adoption flow that
links the central row to the existing provider record (replacing the
stale password with the caller's supplied value and aligning groups to
the assigned role).

Why a separate module
---------------------
Keeps user_write_service.py under the 400-line ratchet and isolates
the provider-scanning cost — this module is the only one that lists
every provider on a create. The signature is deliberately narrow
(one function returning an ``AdoptionCandidate``) so callers don't
accumulate provider state they don't need.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger("media_stack")

_ERR_LEN = 99


@dataclass(frozen=True)
class AdoptionCandidate:
    """An external-provider record that matches a to-be-created user.

    ``provider_name`` is the provider that owns the orphan; ``external_id``
    is that provider's stable key (e.g. the YAML map-key for Authelia,
    the Jellyfin/Jellyseerr integer id). ``match`` records whether we
    matched by username or email — useful for the audit trail, and for
    deciding confidence (username-match on source-of-truth is strong,
    email-match on a secondary is weaker but still actionable).
    """
    provider_name: str
    external_id: str
    match: str  # "username" | "email"
    is_source_of_truth: bool


class OrphanAdoptionFinder:
    """Scans providers for a user matching the create request.

    Takes the list of providers configured on the UserService (already
    constructed by the factory) so this module doesn't need its own
    dependency tree. Thread-safety: read-only against the providers'
    ``list_users`` calls — any caching is the provider's concern.
    """

    def __init__(self, providers, source_of_truth_name: str):
        self._providers = list(providers or [])
        self._sot_name = str(source_of_truth_name or "")

    def find(self, *, username: str, email: str) -> AdoptionCandidate | None:
        """Return the first adoption candidate across all providers, or
        ``None`` if the name is genuinely new.

        Scan order: source-of-truth first, so the strongest signal wins
        when multiple providers have matching rows. Within each
        provider, username-match takes precedence over email-match
        (usernames are the unique key for auth, emails may legitimately
        repeat for test accounts / role aliases).
        """
        target_user = (username or "").strip().lower()
        target_email = (email or "").strip().lower()
        if not target_user and not target_email:
            return None

        ordered = sorted(
            self._providers,
            key=lambda p: 0 if p.name == self._sot_name else 1,
        )
        for provider in ordered:
            candidate = self._scan(provider, target_user, target_email)
            if candidate:
                return candidate
        return None

    def _scan(self, provider, target_user: str, target_email: str
              ) -> AdoptionCandidate | None:
        try:
            externals = list(provider.list_users())
        except Exception as exc:  # noqa: BLE001
            # A provider whose list_users raises shouldn't block the
            # whole create — fall through and let the real create call
            # surface its own error if there was one. Log at DEBUG so
            # we're not spammy during normal operation.
            _log.debug("[DEBUG] adoption scan failed for %s: %s",
                       provider.name, str(exc)[:_ERR_LEN])
            return None
        is_sot = provider.name == self._sot_name
        for ext in externals:
            if (ext.username or "").strip().lower() == target_user and target_user:
                return AdoptionCandidate(
                    provider_name=provider.name,
                    external_id=ext.external_id,
                    match="username", is_source_of_truth=is_sot,
                )
        for ext in externals:
            if (ext.email or "").strip().lower() == target_email and target_email:
                return AdoptionCandidate(
                    provider_name=provider.name,
                    external_id=ext.external_id,
                    match="email", is_source_of_truth=is_sot,
                )
        return None


class ProviderAdopter:
    """Replaces credential + group state on an existing provider record.

    Carries no per-call state — instantiated once at module import as
    ``_INSTANCE`` and exposed through the ``adopt_into_provider``
    module-level alias so callers keep their function-style import
    surface. Plain instance methods (no ``@staticmethod``) per the
    ADR-0012 class-structure ratchet.
    """

    def adopt(self, provider, external_id: str, *,
              password: str, sso_groups: list[str]) -> dict[str, Any]:
        """Replace credential + align groups on an existing provider user.

        Returns a per-step status map the caller can audit. Failures
        here are non-fatal — adoption is better than a dangling ghost,
        so a partial adoption with stale groups still wins over
        rejecting the whole operation.
        """
        status: dict[str, Any] = {}
        try:
            if getattr(provider.capabilities, "supports_password", False):
                provider.set_password(external_id, password)
                status["set_password"] = "ok"
        except Exception as exc:  # noqa: BLE001
            status["set_password"] = f"error: {str(exc)[:_ERR_LEN]}"
        try:
            if getattr(provider.capabilities, "supports_groups", False):
                provider.update_user(external_id, groups=list(sso_groups or []))
                status["update_user"] = "ok"
        except Exception as exc:  # noqa: BLE001
            status["update_user"] = f"error: {str(exc)[:_ERR_LEN]}"
        return status


_INSTANCE = ProviderAdopter()
adopt_into_provider = _INSTANCE.adopt
