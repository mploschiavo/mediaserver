"""Session-visibility protocol implementations for Jellyfin.

Split out of ``user_provider.py`` to keep both files under the
400-line ratchet while cleanly isolating the optional-protocol
surface from the core CRUD.

This module is a mixin class. ``JellyfinApiProvider`` inherits from
``_JellyfinVisibilityMixin`` to pick up:

  * Per-session revoke (``SessionAdminProvider.revoke_session``).
  * Account-state flipping via ``IsDisabled`` on ``/Users/{id}/Policy``
    (``AccountStateProvider``).
  * Conservative MFA reporting — Jellyfin has no server-side 2FA
    surface, so always ``MFAState.none()`` (``MFAStateProvider``).
  * Per-user API-token listing + revocation via ``/Auth/Keys``
    (``APITokenProvider``).

The mixin depends on the parent class exposing ``self._api_key``,
``self._base_url``, ``self._http``, ``self._req``,
``self._apply_policy``, and ``self._require_api_key``. Any
deviation from those names in a future refactor breaks this file —
worth noting in the parent's docstring.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from media_stack.core.auth.users.visibility_protocols import (
    APIToken,
    MFAState,
)

_log = logging.getLogger("media_stack")


class _JellyfinVisibilityMixin:
    """Protocol implementations mixed into ``JellyfinApiProvider``."""

    # ---- SessionAdminProvider -------------------------------------------

    def revoke_session(self, external_id: str, session_id: str) -> None:
        """Kill a single session.

        Tolerates unknown ``session_id`` (404 treated as
        already-gone). ``external_id`` is used as a safety check: we
        verify the session belongs to the user before DELETE, to
        block a confused caller from killing a different user's
        session due to a stale id.
        """
        if not external_id or not session_id or not self._api_key:  # type: ignore[attr-defined]
            return
        try:
            status, body, _ = self._http.request(  # type: ignore[attr-defined]
                self._base_url, "/Sessions", api_key=self._api_key,  # type: ignore[attr-defined]
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] revoke_session list failed: %s", exc)
            return
        if status != HTTPStatus.OK or not isinstance(body, list):
            return
        match = None
        for s in body:
            if (isinstance(s, dict)
                    and str(s.get("Id", "")) == session_id
                    and str(s.get("UserId", "")) == external_id):
                match = s
                break
        if match is None:
            return  # gone or belongs to someone else — no-op
        try:
            self._http.request(  # type: ignore[attr-defined]
                self._base_url, f"/Sessions/{session_id}",  # type: ignore[attr-defined]
                api_key=self._api_key, method="DELETE",  # type: ignore[attr-defined]
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] revoke_session delete failed: %s", exc)

    # ---- AccountStateProvider -------------------------------------------

    def disable_user(self, external_id: str) -> None:
        """Set ``IsDisabled=true`` on the user's Policy.

        Jellyfin's Policy is a single object (not sparse merge) — we
        GET the current policy, flip the flag, POST it back.
        Skipping the GET would clobber library ACLs, session caps,
        and other fields.
        """
        self._update_policy_flag(external_id, "IsDisabled", True)

    def enable_user(self, external_id: str) -> None:
        self._update_policy_flag(external_id, "IsDisabled", False)

    def is_disabled(self, external_id: str) -> bool:
        policy = self._current_policy(external_id)
        if policy is None:
            return False
        return bool(policy.get("IsDisabled", False))

    def _current_policy(self, external_id: str) -> dict[str, Any] | None:
        """GET ``/Users/{id}`` and return the embedded Policy dict, or
        ``None`` when unreachable / malformed. Callers must treat
        ``None`` as 'unknown' — never merge writes over it."""
        if not external_id or not self._api_key:  # type: ignore[attr-defined]
            return None
        try:
            status, body, _ = self._req(f"/Users/{external_id}")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] _current_policy fetch failed: %s", exc)
            return None
        if status != HTTPStatus.OK or not isinstance(body, dict):
            return None
        policy = body.get("Policy")
        return dict(policy) if isinstance(policy, dict) else None

    def _update_policy_flag(
        self, external_id: str, flag: str, value: bool,
    ) -> None:
        """Idempotent flag flip on ``/Users/{id}/Policy``.

        Reads the current policy, updates the single flag, writes
        the full policy back. A missing user causes
        ``JellyfinProviderError`` so callers see a clear error
        rather than silently succeeding.
        """
        if not external_id:
            # Imported lazily to avoid circular import with user_provider.
            from media_stack.services.apps.jellyfin.user_provider import (
                JellyfinProviderError,
            )
            raise JellyfinProviderError("external_id is required")
        self._require_api_key()  # type: ignore[attr-defined]
        policy = self._current_policy(external_id)
        if policy is None:
            from media_stack.services.apps.jellyfin.user_provider import (
                JellyfinProviderError,
            )
            raise JellyfinProviderError(
                f"user {external_id!r} not found or policy unreadable",
            )
        if bool(policy.get(flag, False)) == value:
            return  # already in the desired state — idempotent
        policy[flag] = value
        self._apply_policy(external_id, policy)  # type: ignore[attr-defined]

    # ---- MFAStateProvider -----------------------------------------------

    def mfa_state(self, external_id: str) -> MFAState:
        """Jellyfin has no server-side 2FA surface.

        Jellyfin's auth layer accepts username+password or an API
        key and issues an access token. Per-device 2FA in mobile/TV
        clients is opaque to the server — it cannot report what a
        given client is enforcing. Always ``MFAState.none()``, so
        the UI never implies MFA-backed trust where there isn't any.
        """
        del external_id
        return MFAState.none()

    # ---- APITokenProvider -----------------------------------------------

    def list_api_tokens(self, external_id: str) -> list[APIToken]:
        """List API keys tied to this user via ``/Auth/Keys``.

        Jellyfin's ``/Auth/Keys`` returns every access token the
        server knows about (admin-created API keys, device
        registrations, remote-control authorizations). Each entry
        carries a ``UserId``; we filter to the requested user.

        The raw ``AccessToken`` is **not** returned — this protocol
        exposes metadata only. ``token_id`` uses the ``AccessToken``
        value as the stable key because Jellyfin's delete endpoint
        takes that same value, but we document it as opaque so
        callers don't try to treat it as a secret.
        """
        if not external_id or not self._api_key:  # type: ignore[attr-defined]
            return []
        try:
            status, body, _ = self._req("/Auth/Keys")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] list_api_tokens fetch failed: %s", exc)
            return []
        if status != HTTPStatus.OK:
            return []
        items = self._extract_key_items(body)
        out: list[APIToken] = []
        for item in items:
            if str(item.get("UserId", "")) != external_id:
                continue
            token_id = (
                str(item.get("AccessToken", ""))
                or str(item.get("Id", ""))
                or str(item.get("DeviceId", ""))
            )
            if not token_id:
                continue
            out.append(APIToken(
                token_id=token_id,
                name=str(item.get("AppName", "")),
                created_at=str(item.get("DateCreated", "")),
                last_used_at=str(item.get("DateLastActivity", "")),
                created_by=str(item.get("UserName", "")),
            ))
        return out

    def _extract_key_items(self, body: Any) -> list[dict[str, Any]]:
        """Normalize the ``/Auth/Keys`` response into a flat list of dicts.

        Jellyfin's payload shape has varied across versions — early
        builds returned a bare list, later ones wrap in
        ``{"Items": [...]}``. Both shapes tolerated; anything else
        returns ``[]``.
        """
        del self  # instance method per OO+DI convention; no instance state used
        if isinstance(body, dict):
            raw = body.get("Items") or []
        elif isinstance(body, list):
            raw = body
        else:
            raw = []
        return [item for item in raw if isinstance(item, dict)]

    def revoke_api_token(self, external_id: str, token_id: str) -> None:
        """DELETE ``/Auth/Keys/{token}``.

        Tolerates unknown token (treated as already-gone).
        ``external_id`` is honored as a safety check — a token that
        belongs to a different user is silently skipped rather than
        revoked.
        """
        if not external_id or not token_id or not self._api_key:  # type: ignore[attr-defined]
            return
        tokens = self.list_api_tokens(external_id)
        if not any(t.token_id == token_id for t in tokens):
            return  # already gone, or belongs to another user
        try:
            self._req(  # type: ignore[attr-defined]
                f"/Auth/Keys/{token_id}", method="DELETE",
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] revoke_api_token failed: %s", exc)


__all__ = ["_JellyfinVisibilityMixin"]
