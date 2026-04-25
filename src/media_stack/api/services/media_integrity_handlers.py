"""GET + POST handlers for ``/api/media-integrity/*``.

Five endpoints:
- ``GET  /api/media-integrity/status``         — last-pass summary (any
  authenticated actor; surfaces the Security-tab "media integrity"
  card).
- ``GET  /api/media-integrity/progress``       — poll-able snapshot of
  the in-flight reconcile/enforce pass; the UI hits this every few
  hundred ms while a button is depressed.
- ``POST /api/media-integrity/reconcile``      — trigger a reconciliation
  pass on-demand (admin-only, idempotent, audited).
- ``POST /api/media-integrity/enforce-config`` — trigger a config
  enforcement pass on-demand (admin-only, idempotent, audited).
- ``POST /api/media-integrity/resolve-review`` — operator picks a
  winner for a duplicate the reconciler couldn't auto-resolve;
  deletes losers (admin-only, idempotent, audited).

All routes go through a single ``MediaIntegrityService`` instance held
on this module; production wiring sets the instance at controller-
serve time. Tests construct a service with fake adapters and set it
via ``MediaIntegrityHandlers.set_service(...)``.

Security model
--------------
- CSRF: every mutating endpoint here is CSRF-checked by the global
  POST preflight (``handlers_post.py``); the handler trusts that
  preflight ran. The CSRF ratchet test asserts no media-integrity
  path appears in ``_CSRF_EXEMPT_POST_PATHS``.
- Authz: GET requires authenticated; POSTs require admin.
- Idempotency: every POST consults a shared ``IdempotencyCache``
  keyed on ``(actor.audit_label, Idempotency-Key)``. A second POST
  with the same key inside TTL returns the cached payload with no
  side effects — matches the session-visibility POST handler pattern.
- Concurrency: a duplicate reconcile/enforce trigger arriving while a
  pass is in flight maps to HTTP 409 instead of running twice.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.core.auth.authz import Actor, AuthorizationError
from media_stack.core.auth.idempotency_cache import IdempotencyCache
from media_stack.services.media_integrity.service import (
    MediaIntegrityInProgress,
    MediaIntegrityService,
)


_ERR_LEN = 99


class MediaIntegrityHandlers:
    """Dispatch the media-integrity endpoints.

    Kept tiny because the heavy lifting lives in the service layer;
    this is just a routing + error-mapping shim."""

    _GET_EXACT = (
        "/api/media-integrity/status",
        "/api/media-integrity/progress",
    )
    _POST_EXACT = (
        "/api/media-integrity/reconcile",
        "/api/media-integrity/enforce-config",
        "/api/media-integrity/resolve-review",
    )

    def __init__(
        self,
        *,
        service: MediaIntegrityService | None = None,
        cache: IdempotencyCache | None = None,
    ) -> None:
        self._service = service
        # Match the security POST handlers' tunables — short window
        # because operator retries are seconds-apart, not minutes.
        self._cache = cache if cache is not None else IdempotencyCache(
            ttl_seconds=60, max_entries=128,
        )

    # -- plumbing ------------------------------------------------------

    def set_service(self, service: MediaIntegrityService | None) -> None:
        self._service = service

    def matches_get(self, path: str) -> bool:
        return path in self._GET_EXACT

    def matches_post(self, path: str) -> bool:
        return path in self._POST_EXACT

    # -- GET -----------------------------------------------------------

    def dispatch_get(self, handler: Any, path: str, actor: Actor) -> None:
        if self._service is None:
            handler._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "media-integrity service not configured"},
            )
            return
        if not _is_authenticated(actor):
            handler._json_response(
                HTTPStatus.UNAUTHORIZED, {"error": "authentication required"}
            )
            return
        if path == "/api/media-integrity/status":
            assert self._service is not None
            handler._json_response(HTTPStatus.OK, self._service.status())
            return
        if path == "/api/media-integrity/progress":
            assert self._service is not None
            handler._json_response(HTTPStatus.OK, self._service.get_progress())
            return
        handler._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})

    # -- POST ----------------------------------------------------------

    def dispatch_post(
        self, handler: Any, path: str, body: dict, actor: Actor
    ) -> None:
        if self._service is None:
            handler._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "media-integrity service not configured"},
            )
            return
        try:
            self._require_admin(actor)
        except AuthorizationError as exc:
            handler._json_response(
                HTTPStatus.FORBIDDEN, {"error": str(exc)[:_ERR_LEN]},
            )
            return
        # Split path from query string. Routes are matched on the bare
        # path; ``?dry_run=1`` etc. are parsed into ``query``.
        bare_path, _, raw_qs = path.partition("?")
        query = _parse_query(raw_qs)
        if bare_path not in self._POST_EXACT:
            handler._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        idem_key = self._idem_key(handler)
        cached = self._cache.get(actor.audit_label, idem_key)
        if cached is not None:
            handler._json_response(HTTPStatus.OK, cached)
            return

        try:
            payload = self._run_post(bare_path, body or {}, actor, query)
        except MediaIntegrityInProgress:
            handler._json_response(
                HTTPStatus.CONFLICT, {"error": "already in progress"},
            )
            return
        except ValueError as exc:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
            return

        self._cache.put(actor.audit_label, idem_key, payload)
        handler._json_response(HTTPStatus.OK, payload)

    # -- routing -------------------------------------------------------

    def _run_post(
        self, path: str, body: dict, actor: Actor, query: dict[str, str],
    ) -> dict[str, Any]:
        assert self._service is not None
        if path == "/api/media-integrity/reconcile":
            dry_run = query.get("dry_run", "") in ("1", "true", "yes")
            return self._service.reconcile(
                actor=actor.audit_label, dry_run=dry_run,
            )
        if path == "/api/media-integrity/enforce-config":
            return self._service.enforce_config(actor=actor.audit_label)
        if path == "/api/media-integrity/resolve-review":
            return self._run_resolve_review(body, actor)
        # Defensive — _POST_EXACT is checked in dispatch_post.
        raise ValueError("unknown endpoint")

    def _run_resolve_review(
        self, body: dict, actor: Actor,
    ) -> dict[str, Any]:
        assert self._service is not None
        app = str(body.get("app", "") or "").strip()
        release_id = str(body.get("release_id", "") or "").strip()
        if not app:
            raise ValueError("app is required")
        if not release_id:
            raise ValueError("release_id is required")
        winner_file_id = body.get("winner_file_id")
        winner_sub_path = body.get("winner_sub_path")
        if winner_file_id is None and winner_sub_path is None:
            raise ValueError(
                "winner_file_id or winner_sub_path required",
            )
        return self._service.resolve_review(
            app,
            release_id,
            winner_file_id=(
                str(winner_file_id) if winner_file_id is not None else None
            ),
            winner_sub_path=(
                str(winner_sub_path) if winner_sub_path is not None else None
            ),
            release_kind=(
                str(body["release_kind"]) if body.get("release_kind") else None
            ),
            language=(
                str(body["language"]) if body.get("language") else None
            ),
            forced=bool(body.get("forced", False)),
            hi=bool(body.get("hi", False)),
            actor=actor.audit_label,
        )

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _idem_key(handler: Any) -> str:
        headers = getattr(handler, "headers", None)
        if headers is None:
            return ""
        try:
            return str(headers.get("Idempotency-Key", "") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _require_admin(actor: Actor) -> None:
        """Explicit check mirroring ``@requires_admin`` without needing
        the decorator plumbing (which expects a service method
        signature)."""
        if not actor.is_admin:
            raise AuthorizationError("admin required")


def _is_authenticated(actor: Actor) -> bool:
    """A resolved actor with a non-empty username is authenticated.

    We deliberately do NOT require ``is_admin`` here: Security-tab
    observers with read-only role still see the media-integrity
    status card."""
    return bool(actor and actor.is_authenticated)


def _parse_query(raw: str) -> dict[str, str]:
    """Parse a raw query string (no leading ``?``) into a flat dict.

    Last-write wins on duplicate keys. Only single-value pairs are
    needed; anything richer (multi-value, arrays) is out of scope
    for the current media-integrity surface."""
    out: dict[str, str] = {}
    if not raw:
        return out
    from urllib.parse import parse_qsl
    for k, v in parse_qsl(raw, keep_blank_values=True):
        out[k] = v
    return out


_instance = MediaIntegrityHandlers()


__all__ = [
    "MediaIntegrityHandlers",
    "_instance",
]
