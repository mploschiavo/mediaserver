"""Request-scoped helpers shared by every session-visibility handler.

Owns authz checks, the idempotency-cache lookup/put pair, current-
session resolution, audit writes, and event publication. Split out
so :class:`SecurityPostHandlers` stays under the 15-method size
ratchet.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from media_stack.api.session_singletons import SESSION_COOKIE_NAME
from media_stack.core.auth.authz import Actor, AuthorizationError
from media_stack.core.auth.idempotency_cache import IdempotencyCacheRegistry
from media_stack.core.auth.users.ban_store import BanReason
from media_stack.core.events.bus import Event, EventBus

_log = logging.getLogger("media_stack.api.security_request_context")
_IDEMPOTENCY_HEADER = "Idempotency-Key"


class BadRequest(Exception):
    """Raised by handlers when the caller's payload is invalid."""


class NotFound(Exception):
    """Raised by handlers when the target does not exist."""


class SecurityRequestContext:
    """Cross-cutting helpers used by every handler branch."""

    def __init__(self, *, session_store: Any,
                 user_service_builder: Callable[[], Any],
                 cache: Any, event_bus: EventBus | None,
                 event_bus_fallback: Callable[[], EventBus]) -> None:
        self._session_store = session_store
        self._user_service_builder = user_service_builder
        self._cache = cache
        self._bus = event_bus
        self._bus_fallback = event_bus_fallback

    def require_admin(self, actor: Actor) -> None:
        if not actor.is_admin:
            raise AuthorizationError("admin_required",
                                      f"actor={actor.audit_label}")

    def require_authenticated(self, actor: Actor) -> None:
        if not (actor.is_authenticated or actor.is_system):
            raise AuthorizationError("authentication_required")

    def idem_key(self, handler: Any) -> str:
        try:
            return str(handler.headers.get(_IDEMPOTENCY_HEADER, "")
                       or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def cache_get(self, actor: Actor, key: str) -> dict | None:
        if not key:
            return None
        cache = self._cache or IdempotencyCacheRegistry.get_default()
        return cache.get(actor.audit_label, key)

    def cache_put(self, actor: Actor, key: str, payload: dict) -> None:
        if not key:
            return
        cache = self._cache or IdempotencyCacheRegistry.get_default()
        cache.put(actor.audit_label, key, payload)

    def current_session_id(self, handler: Any) -> str:
        headers = getattr(handler, "headers", None)
        if headers is None:
            return ""
        try:
            raw = headers.get("Cookie", "") or ""
        except AttributeError:
            return ""
        for chunk in raw.split(";"):
            k, _, v = chunk.strip().partition("=")
            if k == SESSION_COOKIE_NAME and v:
                sess = self._session_store.get(v.strip())
                if sess is not None:
                    return str(getattr(sess, "id", "") or "")
        return ""

    def audit(self, actor: Actor, action: str, *, target: str,
              detail: dict[str, Any]) -> None:
        try:
            svc = self._user_service_builder()
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] audit svc: %s", exc)
            return
        if svc is None:
            return
        try:
            svc._audit.append(
                actor=actor.audit_label, action=action, target=target,
                result="ok", ip=actor.client_ip,
                user_agent=actor.user_agent, detail=detail)
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] audit: %s", exc)

    def publish(self, event: Event) -> None:
        try:
            bus = self._bus or self._bus_fallback()
            bus.publish(event)
        except Exception as exc:  # noqa: BLE001
            _log.debug("[DEBUG] publish %s: %s",
                       getattr(event, "event_type", "?"), exc)

    def parse_reason(self, raw: Any) -> BanReason:
        if isinstance(raw, BanReason):
            return raw
        try:
            return BanReason(str(raw or "other").lower())
        except ValueError:
            raise BadRequest(
                f"unknown reason: {raw!r}; valid: "
                f"{[r.value for r in BanReason]}")


__all__ = ["BadRequest", "NotFound", "SecurityRequestContext"]
