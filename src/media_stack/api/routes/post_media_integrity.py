"""Media-integrity POST routes (ADR-0007 Phase 2 wave 8 group 2).

Migrates the three media-integrity write endpoints off the
``handlers_post.handle()`` elif chain onto the OpenAPI Router.
The legacy chain delegated through
``_dispatch_media_integrity_via_job`` (in handlers_post.py) which
shims the heavy work onto ``JobRunner.run`` while preserving the
legacy admin/idempotency/409 contract. This module re-applies the
CSRF gate (the legacy ``_global_preflight`` is bypassed when the
Router dispatches), parses the body, resolves the actor, then
delegates to the same ``_dispatch_media_integrity_via_job`` so the
``MediaIntegrityHandlers`` singleton continues to own the audit
log + idempotency cache.

Routes:

* ``POST /api/media-integrity/reconcile``        — supports
  ``?dry_run=1`` query param (read off ``handler.path`` since
  the Router strips the query before path-matching).
* ``POST /api/media-integrity/enforce-config``
* ``POST /api/media-integrity/resolve-review``

OO discipline:

* ``MediaIntegrityPostRoutes`` is a ``RouteModule`` subclass
  with instance methods only. Constructor-injects the dispatch
  callable + actor resolver + mutation gate so tests can swap
  any collaborator without monkey-patching.

Anti-pattern guard rails:

* No lazy-cache resolver shape — every adapter's default path
  does a fresh module attribute lookup per call so a
  ``mock.patch`` of the canonical symbol takes effect.
* The reconcile route reads ``handler.path`` (not
  ``match.path``) to keep the ``?dry_run=1`` query string
  visible — see the bug-class
  ``test_dispatch_strips_query_string_ratchet`` for context.
"""

from __future__ import annotations

from typing import Any, Callable


from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routing import RouteModule, post


class _MediaIntegrityDispatcher:
    """Adapter onto the legacy ``_dispatch_media_integrity_via_job``
    helper.

    The default path does a fresh module attribute lookup on the
    canonical site (``media_stack.api.handlers_post``) per call so
    a ``mock.patch`` of the canonical symbol takes effect.
    """

    def __init__(
        self,
        dispatch_fn: Callable[..., None] | None = None,
    ) -> None:
        self._dispatch_fn = dispatch_fn

    def dispatch(
        self,
        handler: Any,
        path: str,
        body: dict[str, Any],
        actor: Any,
    ) -> None:
        if self._dispatch_fn is not None:
            self._dispatch_fn(handler, path, body, actor)
            return
        from media_stack.api.services.media_integrity_dispatch import (
            _dispatch_media_integrity_via_job,
        )
        _dispatch_media_integrity_via_job(handler, path, body, actor)


class _ActorResolverProvider:
    """Adapter onto the ``_HandlerActorResolverFactory`` the legacy
    chain uses — kept module-default fall-through so the
    Router's zero-arg auto-discovery works.
    """

    def __init__(
        self,
        resolver: Any = None,
    ) -> None:
        self._resolver = resolver

    def resolve(self, handler: Any, body: dict[str, Any]) -> Any:
        if self._resolver is not None:
            return self._resolver.resolve(handler, body)
        from media_stack.api.services.actor import _actor_resolver
        return _actor_resolver.resolve(handler, body)


class MediaIntegrityPostRoutes(RouteModule):
    """Media-integrity write endpoints — admin-only, idempotency-
    aware, history-tracked through ``JobRunner``.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Constructor defaults keep
    auto-discovery zero-arg while letting tests swap any
    collaborator.
    """

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        dispatcher: _MediaIntegrityDispatcher | None = None,
        actor_resolver_provider: _ActorResolverProvider | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._dispatcher = dispatcher or _MediaIntegrityDispatcher()
        self._actor_resolver = (
            actor_resolver_provider or _ActorResolverProvider()
        )

    # --- gate helper ---------------------------------------------------

    def _gated(self, handler: Any) -> bool:
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    def _dispatch(
        self, handler: Any, full_path: str,
    ) -> None:
        """Read body, resolve actor, delegate to the
        media-integrity job dispatcher.

        ``full_path`` should include the query string so the
        downstream ``_dispatch_media_integrity_via_job`` can parse
        ``?dry_run=1`` for the reconcile branch. The Router
        strips the query string before path-matching, so the
        reconcile handler reads ``handler.path`` (which still
        carries the query) directly.
        """
        body = handler._read_json_body() or {}
        actor = self._actor_resolver.resolve(handler, body)
        self._dispatcher.dispatch(handler, full_path, body, actor)

    # --- routes --------------------------------------------------------

    @post("/api/media-integrity/reconcile")
    def handle_reconcile(self, handler: Any) -> None:
        """Reconcile media library against arr-side state.

        Honors ``?dry_run=1`` — dry runs stay read-only (no
        ``JobRunner`` history entry). The query string is
        forwarded via ``handler.path`` since the Router strips
        the query before path-matching.
        """
        if not self._gated(handler):
            return
        # ``handler.path`` retains the query string so the
        # downstream branch sees ``?dry_run=1``.
        self._dispatch(handler, handler.path)

    @post("/api/media-integrity/enforce-config")
    def handle_enforce_config(self, handler: Any) -> None:
        """Enforce target media-integrity config across libraries."""
        if not self._gated(handler):
            return
        self._dispatch(handler, "/api/media-integrity/enforce-config")

    @post("/api/media-integrity/resolve-review")
    def handle_resolve_review(self, handler: Any) -> None:
        """Resolve a queued media-integrity review.

        Body: ``{app: str, release_id: str, winner_file_id?: int,
        winner_sub_path?: str, release_kind?, language?,
        forced?, hi?}``. Validation flows through the legacy
        ``_resolve_review_via_job`` helper.
        """
        if not self._gated(handler):
            return
        self._dispatch(handler, "/api/media-integrity/resolve-review")


__all__ = [
    "MediaIntegrityPostRoutes",
    "_ActorResolverProvider",
    "_MediaIntegrityDispatcher",
]
