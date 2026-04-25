"""Default service getters + shared plumbing for session-visibility GETs.

Kept in a dedicated module so ``security_get_handlers.py`` stays
under the 400-line ratchet. Each getter is a thin factory so tests
can inject stubs via the helper's constructor; the plumbing class
(actor-resolve, query-parse, error-translation) is reused from the
handler methods and owns no mutable state.
"""

from __future__ import annotations

import logging
import os
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from media_stack.api.actor_resolver import ActorResolver
from media_stack.api.session_singletons import (
    SESSION_COOKIE_NAME,
    session_store,
    trusted_proxy_auth,
)
from media_stack.core.auth.authz import Actor, AuthorizationError
from media_stack.core.auth.users.ban_store import BanStore
from media_stack.core.auth.users.user_service_factory import (
    build_default_service,
)
from media_stack.core.auth.users.visibility_protocols import MFAState

_log = logging.getLogger("media_stack.api.security_get_deps")

_ERR_LEN = 120
_MAX_LIMIT = 10_000


class SecurityGetDeps:
    """Namespace for the default dependency getters. Instance methods
    (not loose functions) keep the class-structure ratchet clean."""

    def build_actor_resolver_factory(self) -> "HandlerActorResolverFactory":
        return HandlerActorResolverFactory()

    def default_ban_store(self) -> Any:
        """Return the shared BanStore instance; path per
        ``docs/security-a11y-contract.md``."""
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        return BanStore(config_root / "controller" / "bans.json")

    def default_audit_log(self) -> Any:
        """Return the audit log owned by the default user-service."""
        service = build_default_service()
        audit = getattr(service, "_audit", None)
        if audit is None:
            raise RuntimeError("user_service did not expose an audit log")
        return audit

    def default_mfa_state(self, username: str) -> Any:
        """Return ``MFAState`` for ``username``; best-effort fallback to
        ``MFAState.none()`` when Authelia's sqlite reader isn't wired.
        The sqlite reader is lazy-imported so reduced-footprint
        deploys without authelia don't pay for it on cold start."""
        try:
            db_path = self._authelia_db_path()
            if not db_path.is_file():
                return MFAState.none()
            return self._authelia_admin(db_path).mfa_state(username)
        except Exception as exc:  # noqa: BLE001
            _log.debug("mfa_state fallback (%s): %s", username, exc)
            return MFAState.none()

    def _authelia_admin(self, db_path: Path) -> Any:
        # Isolated method — sqlite reader is heavy and optional. This
        # method's single ImportFrom is the unavoidable cost of that
        # isolation; top-level import would drag authelia's entire
        # submodule into every cold import of this file.
        from media_stack.services.apps.authelia.session_admin import (
            AutheliaSessionAdmin,
        )
        return AutheliaSessionAdmin(db_path)

    def _authelia_db_path(self) -> Path:
        db_env = os.environ.get("AUTHELIA_DB_PATH", "").strip()
        if db_env:
            return Path(db_env)
        return Path(
            os.environ.get("CONFIG_ROOT", "/srv-config"),
        ) / "authelia" / "db.sqlite3"


class HandlerActorResolverFactory:
    """Lazy ``ActorResolver`` builder so test patches of
    ``build_default_service`` / ``trusted_proxy_auth`` take effect.
    A bare ``ActorResolver(build_service=build_default_service)``
    captures the import-time name and sails past a ``patch(...)``."""

    def resolve(self, handler: Any, body: dict | None = None) -> Actor:
        impl = ActorResolver(
            build_service=build_default_service,
            client_ip_for=trusted_proxy_auth.client_ip,
        )
        return impl.resolve(handler, body or {})


class RequestPlumbing:
    """Shared request parsing + error translation.

    One instance per helper; stateless aside from the handler the
    caller passes through. Keeping it on a class instead of loose
    functions honours the LOOSE_FUNCTIONS ratchet.
    """

    def serve(
        self,
        handler: Any,
        actor_resolver: HandlerActorResolverFactory,
        runner: Callable[[Actor], dict],
    ) -> None:
        """Resolve the actor, run the service, translate exceptions."""
        try:
            actor = actor_resolver.resolve(handler, {})
        except AuthorizationError as exc:
            self._deny(handler, exc, anonymous=True)
            return
        try:
            payload = runner(actor)
        except AuthorizationError as exc:
            self._deny(handler, exc, anonymous=not actor.is_authenticated)
            return
        except ValueError as exc:
            self.bad_request(handler, exc)
            return
        except Exception as exc:  # noqa: BLE001
            _log.warning("session-visibility GET failed: %s", exc)
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "internal_error", "detail": self._trim(str(exc))},
            )
            return
        handler._json_response(HTTPStatus.OK, payload)

    def _deny(
        self, handler: Any, exc: AuthorizationError, *, anonymous: bool,
    ) -> None:
        status = HTTPStatus.UNAUTHORIZED if (
            anonymous and exc.reason == "authentication_required"
        ) else HTTPStatus.FORBIDDEN
        handler._json_response(
            status, {"error": exc.reason, "detail": self._trim(exc.detail)},
        )

    def bad_request(self, handler: Any, exc: Exception) -> None:
        handler._json_response(
            HTTPStatus.BAD_REQUEST,
            {"error": "bad_request", "detail": self._trim(str(exc))},
        )

    def require_authenticated(self, actor: Actor) -> None:
        if not actor.is_authenticated:
            raise AuthorizationError(
                "authentication_required",
                "endpoint requires a logged-in user",
            )

    def require_admin(self, actor: Actor) -> None:
        self.require_authenticated(actor)
        if not actor.is_admin:
            raise AuthorizationError(
                "admin_required", f"actor={actor.audit_label}",
            )

    def int_query(self, handler: Any, key: str, default: int) -> int:
        raw = self._qs(handler).get(key, [""])[0]
        if raw == "":
            return int(default)
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{key!r} must be an integer") from exc
        if value < 0 or value > _MAX_LIMIT:
            raise ValueError(
                f"{key!r} must be between 0 and {_MAX_LIMIT}",
            )
        return value

    def bool_query(self, handler: Any, key: str) -> bool:
        raw = self._qs(handler).get(key, [""])[0].strip().lower()
        return raw in ("1", "true", "yes", "on")

    def _qs(self, handler: Any) -> dict[str, list[str]]:
        path = getattr(handler, "path", "") or ""
        return parse_qs(urlparse(path).query, keep_blank_values=True)

    def current_session_id(self, handler: Any) -> str:
        """Return the non-secret session id on this request, or ''.
        Never surfaces cookie plaintext."""
        headers = getattr(handler, "headers", None)
        if headers is None:
            return ""
        try:
            cookie_raw = headers.get("Cookie", "") or ""
        except AttributeError:
            return ""
        for chunk in cookie_raw.split(";"):
            if "=" not in chunk:
                continue
            k, _, v = chunk.strip().partition("=")
            if k != SESSION_COOKIE_NAME:
                continue
            sess = session_store.get(v.strip())
            if sess is not None:
                return str(getattr(sess, "id", "") or "")
        try:
            return str(headers.get("X-Session-Id", "") or "").strip()
        except AttributeError:
            return ""

    def _trim(self, text: str) -> str:
        """Cap error details so payloads stay bounded."""
        s = str(text or "")
        return s if len(s) <= _ERR_LEN else s[:_ERR_LEN] + "..."


__all__ = [
    "HandlerActorResolverFactory",
    "RequestPlumbing",
    "SecurityGetDeps",
]
