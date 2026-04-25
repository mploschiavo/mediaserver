"""Cross-provider cascade helpers used by the security POST handlers.

The companion to :mod:`security_post_handlers` owning the "fan out
to every provider" operations: disable/enable user, add/remove IP
deny rule, revoke every session on every provider, rotate the
controller's bearer-token secret, and flag admin users for forced
password rotation.

Kept separate so the main handler file stays under the file-size
and class-method ratchets.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from media_stack.core.auth.users.ban_store import BanReason
from media_stack.core.auth.users.ip_deny import IPDeny
from media_stack.core.time_utils import utcnow_iso

_log = logging.getLogger("media_stack.api.security_cascades")


class SecurityCascades:
    """Bundle of provider fan-out operations."""

    def __init__(self, *, session_store: Any,
                 user_service_builder: Callable[[], Any],
                 token_store_builder: Callable[[], Any]) -> None:
        self._sessions = session_store
        self._build_svc = user_service_builder
        self._build_tokens = token_store_builder

    def _call(self, target: Any, method_name: str, *args: Any) -> str:
        """Call ``target.method_name(*args)`` and classify the outcome.

        Returns ``"ok"`` on success, ``"unsupported"`` when the method
        is absent, or ``"err: <truncated>"`` on raise. Used to build
        the per-provider status dict that ships in the audit entry.
        """
        fn = getattr(target, method_name, None)
        if fn is None:
            return "unsupported"
        try:
            fn(*args)
            return "ok"
        except Exception as exc:  # noqa: BLE001
            return f"err: {str(exc)[:40]}"

    def user(self, username: str, *, enable: bool) -> dict[str, str]:
        svc = self._svc()
        if svc is None:
            return {"user_service": "err: unavailable"}
        user = self._lookup(svc, username)
        op = "enable_user" if enable else "disable_user"
        results: dict[str, str] = {}
        for p in getattr(svc, "_providers", []) or []:
            name = getattr(p, "name", "?")
            ext = ((user and user.provider_refs.get(name))
                   if user else None) or username
            results[name] = self._call(p, op, ext)
            if not enable and ext:
                self._call(p, "revoke_sessions", ext)
        if not enable:
            n = self._sessions.revoke_all_for(username)
            results["controller_sessions"] = f"revoked:{n}"
        return results

    def ip(self, cidr: str, *, reason: BanReason, expires: str,
           actor_label: str, remove: bool) -> dict[str, str]:
        results: dict[str, str] = {}
        for p in self._ip_deny_providers():
            name = getattr(p, "name", "?")
            if remove:
                results[name] = self._call(p, "remove_ip_deny", cidr)
            else:
                rule = IPDeny(cidr=cidr, reason=reason.value,
                              actor=actor_label, banned_at=utcnow_iso(),
                              expires_at=expires)
                results[name] = self._call(p, "add_ip_deny", rule)
        return results

    def revoke_all_providers(self) -> dict[str, str]:
        results: dict[str, str] = {"controller_sessions": "ok"}
        svc = self._svc()
        if svc is None:
            return {**results, "user_service": "err: unavailable"}
        for p in getattr(svc, "_providers", []) or []:
            name = getattr(p, "name", "?")
            ok = 0
            errors = 0
            try:
                users = svc._store.list_all()
            except Exception:  # noqa: BLE001
                results[name] = "err: user list"
                continue
            for u in users:
                ext = (getattr(u, "provider_refs", {}) or {}).get(name)
                if not ext:
                    continue
                outcome = self._call(p, "revoke_sessions", ext)
                if outcome == "ok":
                    ok += 1
                elif outcome.startswith("err:"):
                    errors += 1
            if errors and not ok:
                results[name] = f"err: {errors} failures"
            elif ok:
                results[name] = f"revoked:{ok}"
            else:
                results[name] = "ok"
        return results

    def rotate_token_secrets(self) -> bool:
        try:
            store = self._build_tokens()
            rotate = getattr(store, "rotate_signing_secret", None)
            if rotate is None:
                return False
            rotate()
            return True
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] rotate_signing_secret: %s", exc)
            return False

    def flag_admins_for_rotation(self) -> int:
        svc = self._svc()
        if svc is None:
            return 0
        count = 0
        try:
            for u in svc._store.list_all():
                role = svc._roles.get(u.role_slug) if getattr(
                    svc, "_roles", None) else None
                if not bool(getattr(role, "controller_admin", False)):
                    continue
                try:
                    svc._store.update(u.id, source="rotated")
                    count += 1
                except Exception as exc:  # noqa: BLE001
                    _log.debug("[DEBUG] flag_admin %s: %s", u.id, exc)
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] flag_admins: %s", exc)
        return count

    def flag_user_for_rotation(self, username: str) -> None:
        svc = self._svc()
        if svc is None:
            return
        user = self._lookup(svc, username)
        if user is None:
            return
        try:
            svc._store.update(user.id, source="rotated")
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] flag_user: %s", exc)

    def _svc(self) -> Any:
        try:
            return self._build_svc()
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] user_service build: %s", exc)
            return None

    def _lookup(self, svc: Any, username: str) -> Any:
        try:
            return svc._store.get_by_username(username)
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] lookup_user: %s", exc)
            return None

    def _ip_deny_providers(self) -> list[Any]:
        svc = self._svc()
        if svc is None:
            return []
        return [p for p in (getattr(svc, "_providers", []) or [])
                if callable(getattr(p, "add_ip_deny", None))]


__all__ = ["SecurityCascades"]
