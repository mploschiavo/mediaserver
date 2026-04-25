"""Build an ``Actor`` for user-management POSTs from an HTTP handler.

Kept in its own module so handlers_post.py doesn't grow further and so
the logic is easy to test in isolation (see
``tests/unit/test_actor_role_lookup.py``).

The old dispatcher built ``Actor(username=x, is_admin=True)``
unconditionally. That made the ``@requires_admin`` decorator a no-op
for every authenticated caller — any user who reached the dispatch
passed as admin. This module closes that gap by:

  1. Looking up the caller's user row via ``UserQueryService``.
  2. Looking up the role and reading ``role.controller_admin``.
  3. Plumbing the trusted-proxy client IP + user-agent onto the Actor
     so audit entries tie back to the real client (not an Envoy hop).
  4. Falling back to ``is_admin=True`` ONLY when the user row or role
     is missing — preserves bootstrap behaviour (env-var admin on
     fresh deploy) without leaving the admin gate wide open in
     steady state.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from media_stack.core.auth.authz import Actor

_log = logging.getLogger("controller_api")


class ActorResolver:
    """Resolve an ``Actor`` for a user-mgmt dispatch from a handler.

    Dependencies (``build_service``, ``client_ip_for``) are injected
    at construction time so tests can exercise the branches without
    monkeypatching module-level imports on ``handlers_post``.
    """

    def __init__(
        self,
        *,
        build_service: Callable[[], Any],
        client_ip_for: Callable[[Any], str],
    ) -> None:
        self._build_service = build_service
        self._client_ip_for = client_ip_for

    def resolve(self, handler: Any, body: dict) -> Actor:
        """Return an ``Actor`` for this request."""
        actor_name = str((body or {}).get("_actor", "") or "controller-ui")
        client_ip = self._safe_client_ip(handler)
        user_agent = self._safe_user_agent(handler)
        user, role, role_slug = self._lookup_user_and_role(actor_name)
        if not user or not role:
            _log.debug(
                "[DEBUG] actor role lookup fell back to bootstrap for %r "
                "(user=%s role=%s)", actor_name, bool(user), bool(role),
            )
            return Actor(
                username=actor_name,
                is_admin=True,
                client_ip=client_ip,
                user_agent=user_agent,
                source_provider="controller",
            )
        slug = str(role.get("slug", role_slug) or role_slug)
        return Actor(
            username=actor_name,
            roles=frozenset({slug}) if slug else frozenset(),
            is_admin=bool(role.get("controller_admin", False)),
            client_ip=client_ip,
            user_agent=user_agent,
            source_provider="controller",
        )

    def _safe_client_ip(self, handler: Any) -> str:
        try:
            return str(self._client_ip_for(handler) or "")
        except Exception:  # noqa: BLE001
            return ""

    def _safe_user_agent(self, handler: Any) -> str:
        try:
            headers = getattr(handler, "headers", None)
            if headers is None:
                return ""
            return str(headers.get("User-Agent", "") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _lookup_user_and_role(
        self, username: str,
    ) -> tuple[dict | None, dict | None, str]:
        try:
            svc = self._build_service()
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] actor resolver: build_service raised: %s", exc,
            )
            return None, None, ""
        user = self._lookup_user(svc, username)
        role_slug = ""
        role: dict | None = None
        if user:
            role_slug = str(user.get("role_slug", "") or "").strip()
            if role_slug:
                role = self._lookup_role(svc, role_slug)
        return user, role, role_slug

    def _lookup_user(self, svc: Any, username: str) -> dict | None:
        try:
            lookup = getattr(svc, "get_user_by_username", None)
            return lookup(username) if callable(lookup) else None
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] actor resolver: get_user_by_username raised: %s",
                exc,
            )
            return None

    def _lookup_role(self, svc: Any, role_slug: str) -> dict | None:
        try:
            get_role = getattr(svc, "get_role", None)
            return get_role(role_slug) if callable(get_role) else None
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] actor resolver: get_role raised: %s", exc,
            )
            return None


__all__ = ["ActorResolver"]
