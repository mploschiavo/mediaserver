"""POST handlers for the session-visibility mutating endpoints.

Eight endpoints — admin session-revoke, user/IP ban add+remove,
emergency revoke-all, and two self-service endpoints — share one
class. Every branch:

  1. Trusts the POST dispatcher's global preflight (rate-limit + CSRF
     + body-size). This module is invoked only after those run.
  2. Resolves an :class:`Actor` via the existing actor resolver.
  3. Consults a shared :class:`IdempotencyCache` keyed by
     ``(actor.audit_label, Idempotency-Key)`` — a repeat inside TTL
     returns the cached payload with no side effects.
  4. Invokes services; :class:`AuthorizationError` → 403.
  5. Writes an audit entry + publishes a typed domain event.
  6. Caches successful responses under the idempotency key.

Hard rule: never return a raw secret (token plaintext, password,
session cookie value) in a response body.
"""

from __future__ import annotations

import json as _json
import os
import re
from http import HTTPStatus
from pathlib import Path as _Path
from typing import Any, Callable

from media_stack.api.services.security_cascades import SecurityCascades
from media_stack.api.services.security_request_context import (
    BadRequest as _BadRequest,
    NotFound as _NotFound,
    SecurityRequestContext,
)
from media_stack.api.session_singletons import (
    SESSION_COOKIE_NAME,
    session_store as _session_store,
)
from media_stack.core.auth.authz import Actor, AuthorizationError
from media_stack.core.auth.users import audit_actions
from media_stack.core.auth.users.ban_store import (
    BanReason, BanStore, BanStoreError, IPBanRecord, UserBan,
)
from media_stack.core.auth.users.user_service_factory import (
    build_default_api_token_store, build_default_service,
)
from media_stack.core.events import (
    BanApplied, BanRemoved, EmergencyRevokeInvoked, LoginBlocked,
    SessionRevoked,
)
from media_stack.core.events.bus import EventBus
from media_stack.core.time_utils import utcnow_iso

_ERR_LEN = 99
_PROVIDER = "controller"

_default_ban_store: Any = None


class _SecurityEventBusRegistry:
    """Accessor for the shared :class:`EventBus`. Tests inject via set()."""

    _bus: EventBus | None = None

    @classmethod
    def get(cls) -> EventBus:
        if cls._bus is None:
            cls._bus = EventBus()
        return cls._bus

    @classmethod
    def set(cls, bus: EventBus | None) -> None:
        cls._bus = bus


class _BanStoreFactory:
    """Lazy shared :class:`BanStore`."""

    def get(self) -> Any:
        global _default_ban_store
        if _default_ban_store is None:
            root = _Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
            path = root / "controller" / "bans.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            _default_ban_store = BanStore(path)
        return _default_ban_store

    def set(self, store: Any) -> None:
        global _default_ban_store
        _default_ban_store = store


_ban_store_factory = _BanStoreFactory()


class SecurityPostHandlers:
    """Dispatch table for the eight session-visibility POST endpoints."""

    _EXACT: tuple[str, ...] = (
        "/api/bans/users", "/api/bans/ips",
        "/api/emergency-revoke-all",
        "/api/me/revoke-others", "/api/me/this-wasnt-me",
    )
    _REVOKE_RE = re.compile(
        r"^/api/users/(?P<user_id>[^/]+)/sessions/"
        r"(?P<session_id>[^/]+)/revoke$",
    )
    _UBAN_REM_RE = re.compile(r"^/api/bans/users/(?P<username>[^/]+)/remove$")
    _IBAN_REM_RE = re.compile(r"^/api/bans/ips/(?P<cidr>[^/]+)/remove$")

    def __init__(
        self, *,
        ban_store_getter: Callable[[], Any] | None = None,
        session_store: Any = None,
        token_store_builder: Callable[[], Any] | None = None,
        user_service_builder: Callable[[], Any] | None = None,
        cache: Any = None, event_bus: EventBus | None = None,
    ) -> None:
        self._ban_store_getter = ban_store_getter or _ban_store_factory.get
        self._session_store = session_store or _session_store
        user_builder = user_service_builder or build_default_service
        self._ctx = SecurityRequestContext(
            session_store=self._session_store,
            user_service_builder=user_builder,
            cache=cache, event_bus=event_bus,
            event_bus_fallback=_SecurityEventBusRegistry.get,
        )
        self._cascades = SecurityCascades(
            session_store=self._session_store,
            user_service_builder=user_builder,
            token_store_builder=(
                token_store_builder or build_default_api_token_store),
        )

    def matches(self, path: str) -> bool:
        return (path in self._EXACT
                or self._REVOKE_RE.match(path) is not None
                or self._UBAN_REM_RE.match(path) is not None
                or self._IBAN_REM_RE.match(path) is not None)

    def dispatch(self, handler: Any, path: str, body: dict,
                 actor: Actor) -> None:
        """Route + execute + emit response."""
        idem_key = self._ctx.idem_key(handler)
        cached = self._ctx.cache_get(actor, idem_key)
        if cached is not None:
            handler._json_response(HTTPStatus.OK, cached)
            return
        try:
            status, payload, extra = self._route(handler, path, body or {},
                                                 actor)
        except AuthorizationError as exc:
            handler._json_response(HTTPStatus.FORBIDDEN,
                                    {"error": str(exc)[:_ERR_LEN]})
            return
        except _BadRequest as exc:
            handler._json_response(HTTPStatus.BAD_REQUEST,
                                    {"error": str(exc)[:_ERR_LEN]})
            return
        except _NotFound as exc:
            handler._json_response(HTTPStatus.NOT_FOUND,
                                    {"error": str(exc)[:_ERR_LEN]})
            return
        if status == HTTPStatus.OK:
            self._ctx.cache_put(actor, idem_key, payload)
        if extra:
            handler._raw_response(
                status, "application/json",
                _json.dumps(payload, default=str).encode("utf-8"), extra,
            )
            return
        handler._json_response(status, payload)

    # ---- routing ---------------------------------------------------------

    def _route(self, handler: Any, path: str, body: dict,
               actor: Actor) -> tuple[int, dict, dict[str, str] | None]:
        m = self._REVOKE_RE.match(path)
        if m:
            return self._revoke_session(m.group("user_id"),
                                         m.group("session_id"), body, actor)
        if path == "/api/bans/users":
            return self._ban_user_add(handler, body, actor)
        m = self._UBAN_REM_RE.match(path)
        if m:
            return self._ban_user_remove(m.group("username"), actor)
        if path == "/api/bans/ips":
            return self._ban_ip_add(handler, body, actor)
        m = self._IBAN_REM_RE.match(path)
        if m:
            return self._ban_ip_remove(m.group("cidr"), actor)
        if path == "/api/emergency-revoke-all":
            return self._emergency_revoke_all(body, actor)
        if path == "/api/me/revoke-others":
            return self._me_revoke_others(handler, actor)
        if path == "/api/me/this-wasnt-me":
            return self._me_this_wasnt_me(body, actor)
        raise _NotFound("unknown security endpoint")

    # ---- endpoint implementations ---------------------------------------

    def _revoke_session(self, user_id: str, session_id: str, body: dict,
                        actor: Actor):
        self._ctx.require_admin(actor)
        reason = str(body.get("reason", "") or "admin_revoke").strip()
        if not self._session_store.revoke_by_id(session_id, reason=reason):
            raise _NotFound(f"session {session_id} not found")
        self._ctx.audit(actor, audit_actions.SESSION_REVOKED, target=session_id,
                    detail={"user_id": user_id, "reason": reason})
        self._ctx.publish(SessionRevoked(
            username=str(user_id), session_id=session_id,
            provider=_PROVIDER, reason=reason))
        return HTTPStatus.OK, {"ok": True, "session_id": session_id}, None

    def _ban_user_add(self, handler: Any, body: dict, actor: Actor):
        self._ctx.require_admin(actor)
        username = str(body.get("username", "") or "").strip()
        if not username:
            raise _BadRequest("username is required")
        reason = self._ctx.parse_reason(body.get("reason"))
        detail_s = str(body.get("reason_detail", "") or "")
        expires = str(body.get("expires_at", "") or "")
        idem = self._ctx.idem_key(handler)
        ban = UserBan(
            username=username, reason=reason, reason_detail=detail_s,
            actor=actor.audit_label, banned_at=utcnow_iso(),
            expires_at=expires, idempotency_key=idem)
        try:
            stored = self._ban_store_getter().add_user_ban(ban)
        except BanStoreError as exc:
            raise _BadRequest(str(exc))
        cascades = self._cascades.user(username, enable=False)
        self._ctx.audit(actor, audit_actions.BAN_USER_ADD, target=username,
                    detail={"reason": reason.value, "cascades": cascades,
                            "expires_at": expires})
        self._ctx.publish(BanApplied(kind="user", target=username,
                                  actor=actor.audit_label,
                                  reason=reason.value, expires_at=expires))
        return HTTPStatus.OK, {**stored.to_dict(), "cascades": cascades}, None

    def _ban_user_remove(self, username: str, actor: Actor):
        self._ctx.require_admin(actor)
        removed = self._ban_store_getter().remove_user_ban(username)
        cascades = self._cascades.user(username, enable=True)
        self._ctx.audit(actor, audit_actions.BAN_USER_REMOVE, target=username,
                    detail={"cascades": cascades, "removed": bool(removed)})
        self._ctx.publish(BanRemoved(kind="user", target=username,
                                  actor=actor.audit_label))
        return HTTPStatus.OK, {
            "ok": True, "username": username,
            "removed": bool(removed), "cascades": cascades}, None

    def _ban_ip_add(self, handler: Any, body: dict, actor: Actor):
        self._ctx.require_admin(actor)
        cidr = str(body.get("cidr", "") or "").strip()
        if not cidr:
            raise _BadRequest("cidr is required")
        reason = self._ctx.parse_reason(body.get("reason"))
        detail_s = str(body.get("reason_detail", "") or "")
        expires = str(body.get("expires_at", "") or "")
        idem = self._ctx.idem_key(handler)
        try:
            ban = IPBanRecord(
                cidr=cidr, reason=reason, reason_detail=detail_s,
                actor=actor.audit_label, banned_at=utcnow_iso(),
                expires_at=expires, idempotency_key=idem)
        except ValueError as exc:
            raise _BadRequest(f"invalid cidr: {exc}")
        try:
            stored = self._ban_store_getter().add_ip_ban(ban)
        except BanStoreError as exc:
            raise _BadRequest(str(exc))
        cascades = self._cascades.ip(
            stored.cidr, reason=reason, expires=expires,
            actor_label=actor.audit_label, remove=False)
        self._ctx.audit(actor, audit_actions.BAN_IP_ADD, target=stored.cidr,
                    detail={"reason": reason.value, "cascades": cascades,
                            "expires_at": expires})
        self._ctx.publish(BanApplied(kind="ip", target=stored.cidr,
                                  actor=actor.audit_label,
                                  reason=reason.value, expires_at=expires))
        return HTTPStatus.OK, {**stored.to_dict(), "cascades": cascades}, None

    def _ban_ip_remove(self, cidr: str, actor: Actor):
        self._ctx.require_admin(actor)
        removed = self._ban_store_getter().remove_ip_ban(cidr)
        cascades = self._cascades.ip(cidr, reason=BanReason.OTHER,
                                      expires="", actor_label="",
                                      remove=True)
        self._ctx.audit(actor, audit_actions.BAN_IP_REMOVE, target=cidr,
                    detail={"cascades": cascades, "removed": bool(removed)})
        self._ctx.publish(BanRemoved(kind="ip", target=cidr,
                                  actor=actor.audit_label))
        return HTTPStatus.OK, {
            "ok": True, "cidr": cidr,
            "removed": bool(removed), "cascades": cascades}, None

    def _emergency_revoke_all(self, body: dict, actor: Actor):
        self._ctx.require_admin(actor)
        reason = str(body.get("reason", "") or "").strip()
        if len(reason) < 5:
            raise _BadRequest("reason is required (min 5 chars)")
        killed = list(self._session_store.revoke_all(
            reason="emergency_revoke"))
        providers = self._cascades.revoke_all_providers()
        rotated = self._cascades.rotate_token_secrets()
        forced = self._cascades.flag_admins_for_rotation()
        self._ctx.audit(actor, audit_actions.EMERGENCY_REVOKE_ALL,
                    target="all-sessions",
                    detail={"reason": reason,
                            "sessions_revoked": len(killed),
                            "provider_results": providers,
                            "secrets_rotated": rotated,
                            "forced_rotations": forced})
        self._ctx.publish(EmergencyRevokeInvoked(
            actor=actor.audit_label, reason=reason,
            sessions_revoked=len(killed),
            forced_rotations=forced, secrets_rotated=rotated))
        all_ok = all(v == "ok" for v in providers.values())
        return HTTPStatus.OK, {
            "ok": all_ok, "provider_results": providers,
            "secrets_rotated": rotated,
            "forced_rotations": forced}, None

    def _me_revoke_others(self, handler: Any, actor: Actor):
        self._ctx.require_authenticated(actor)
        current_id = self._ctx.current_session_id(handler)
        killed: list[str] = []
        for sess in self._session_store.list_for(actor.username):
            if sess.id == current_id:
                continue
            if self._session_store.revoke_by_id(
                    sess.id, reason="user_revoke_others"):
                killed.append(sess.id)
                self._ctx.audit(actor, audit_actions.SESSION_REVOKED,
                            target=sess.id,
                            detail={"reason": "user_revoke_others"})
                self._ctx.publish(SessionRevoked(
                    username=actor.username, session_id=sess.id,
                    provider=_PROVIDER, reason="user_revoke_others"))
        return HTTPStatus.OK, {
            "ok": True, "revoked": killed, "count": len(killed)}, None

    def _me_this_wasnt_me(self, body: dict, actor: Actor):
        self._ctx.require_authenticated(actor)
        flagged_ip = str(body.get("flagged_ip", "") or "")
        login_ts = str(body.get("login_timestamp", "") or "")
        killed = int(self._session_store.revoke_all_for(actor.username))
        self._cascades.flag_user_for_rotation(actor.username)
        self._ctx.audit(actor, audit_actions.ANOMALY_CREDENTIAL_STUFFING,
                    target=actor.username,
                    detail={"flagged_ip": flagged_ip,
                            "login_timestamp": login_ts,
                            "sessions_revoked": killed})
        self._ctx.publish(LoginBlocked(
            username=actor.username, client_ip=flagged_ip,
            ban_kind="anomaly", ban_reason="this_wasnt_me"))
        clear = (f"{SESSION_COOKIE_NAME}=; HttpOnly; Secure; "
                 "SameSite=Strict; Path=/; Max-Age=0")
        return HTTPStatus.OK, {"ok": True}, {"Set-Cookie": clear}



_security_post_handlers = SecurityPostHandlers()

__all__ = [
    "SecurityPostHandlers",
    "_BanStoreFactory",
    "_SecurityEventBusRegistry",
    "_security_post_handlers",
]
