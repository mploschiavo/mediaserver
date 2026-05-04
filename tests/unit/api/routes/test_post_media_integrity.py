"""Tests for ``api/routes/post_media_integrity.py`` (ADR-0007 Phase 2 wave 8 group 2).

Covers the three media-integrity write POST routes lifted off the
legacy ``handlers_post`` elif chain. The route layer delegates
business logic to ``_dispatch_media_integrity_via_job`` which owns
admin gating, idempotency caching, 409 mapping, and JobRunner
integration. These tests focus on the delegation boundary:

* CSRF gate is enforced (403 when rejected, body untouched).
* Body parsing + actor resolution flow through.
* The ``?dry_run=1`` query string is preserved on the reconcile
  branch via ``handler.path``.
* Auto-discovery + spec-parity for the three paths.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_media_integrity import (
    MediaIntegrityPostRoutes,
    _ActorResolverProvider,
    _MediaIntegrityDispatcher,
)
from media_stack.api.routing import (
    DefaultDispatcher,
    DispatchOutcome,
    Router,
    RouterDispatcher,
)
from tests.unit.api.routes._helpers import (
    CapturedResponse,
    MockControllerHandler,
    RouteDispatchHarness,
)


class PostMockHandler(MockControllerHandler):
    def __init__(
        self,
        *,
        path: str = "/",
        body: bytes | dict[str, Any] = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        merged_headers = dict(headers or {})
        if body and "Content-Length" not in merged_headers:
            merged_headers["Content-Length"] = str(len(body))
        super().__init__(
            path=path, body=body, headers=merged_headers,
        )

    def _read_json_body(self) -> dict[str, Any]:
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return {}


class _AlwaysAllowGate:
    def verify(self, handler: Any) -> bool:
        return True

    def reject(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError("verify should have returned True")


class _StubDispatcher:
    """Captures ``dispatch`` invocations + emits a sentinel
    response so tests assert the route layer forwarded
    correctly without exercising the JobRunner stack."""

    def __init__(
        self,
        response_body: dict[str, Any] | None = None,
        response_status: int = 200,
    ) -> None:
        self._response_body = response_body or {"status": "ok"}
        self._response_status = response_status
        self.calls: list[tuple[Any, str, dict[str, Any], Any]] = []

    def dispatch(
        self,
        handler: Any,
        path: str,
        body: dict[str, Any],
        actor: Any,
    ) -> None:
        self.calls.append((handler, path, body, actor))
        handler._json_response(self._response_status, self._response_body)


class _StubActorResolver:
    def __init__(self, actor: Any = None) -> None:
        self.actor = actor or object()
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def resolve(self, handler: Any, body: dict[str, Any]) -> Any:
        self.calls.append((handler, body))
        return self.actor


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _RouteHarness:
    @classmethod
    def with_routes(
        cls, routes: MediaIntegrityPostRoutes,
    ) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(
        cls, router: Router, routes: MediaIntegrityPostRoutes,
    ) -> None:
        for key, route in list(router._exact.items()):
            replacement = cls._maybe_replacement(route, routes)
            if replacement is not None:
                router._exact[key] = type(route)(
                    verb=route.verb, path=route.path,
                    handler=replacement, pattern=route.pattern,
                    param_names=route.param_names, display=route.display,
                )
        for idx, route in enumerate(list(router._parameterized)):
            replacement = cls._maybe_replacement(route, routes)
            if replacement is not None:
                router._parameterized[idx] = type(route)(
                    verb=route.verb, path=route.path,
                    handler=replacement, pattern=route.pattern,
                    param_names=route.param_names, display=route.display,
                )

    @staticmethod
    def _maybe_replacement(
        route: Any, routes: MediaIntegrityPostRoutes,
    ) -> Any:
        if "MediaIntegrityPostRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name)


def _dispatch_post(
    harness: RouteDispatchHarness,
    path: str,
    *,
    body: bytes | dict[str, Any] = b"",
    headers: dict[str, str] | None = None,
) -> CapturedResponse:
    handler = PostMockHandler(path=path, body=body, headers=headers)
    # The Router strips the query string before path-matching;
    # mirror that behaviour at the dispatch boundary so the
    # reconcile branch can read ``handler.path`` for the query.
    bare_path = path.split("?", 1)[0]
    outcome = harness._dispatcher.try_dispatch("POST", bare_path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        harness._dispatcher.write_method_not_allowed(handler, bare_path)
    return handler.captured


def _routes_with(
    *,
    dispatcher: _StubDispatcher,
    actor: _StubActorResolver,
) -> MediaIntegrityPostRoutes:
    return MediaIntegrityPostRoutes(
        mutation_gate=_AlwaysAllowGate(),
        dispatcher=_MediaIntegrityDispatcher(
            dispatch_fn=dispatcher.dispatch,
        ),
        actor_resolver_provider=_ActorResolverProvider(resolver=actor),
    )


# ---------------------------------------------------------------------------
# /api/media-integrity/reconcile
# ---------------------------------------------------------------------------


class TestReconcileRoute:
    def test_forwards_body_and_actor(self) -> None:
        disp = _StubDispatcher(response_body={"status": "reconciled"})
        actor = _StubActorResolver()
        harness = _RouteHarness.with_routes(_routes_with(
            dispatcher=disp, actor=actor,
        ))
        response = _dispatch_post(
            harness, "/api/media-integrity/reconcile",
            body={"some": "field"},
        )

        assert response.status == 200
        assert json.loads(response.body) == {"status": "reconciled"}
        assert len(disp.calls) == 1
        _, path, body, dispatched_actor = disp.calls[0]
        assert path == "/api/media-integrity/reconcile"
        assert body == {"some": "field"}
        assert dispatched_actor is actor.actor

    def test_dry_run_query_string_preserved(self) -> None:
        """Pin that ``?dry_run=1`` is forwarded to the dispatcher
        via ``handler.path``. The Router strips the query before
        path-matching, but the reconcile route reads
        ``handler.path`` (which retains the query) directly so
        the JobRunner shim can branch on dry-run.
        """
        disp = _StubDispatcher()
        actor = _StubActorResolver()
        routes = _routes_with(dispatcher=disp, actor=actor)

        # Manually invoke handler — the harness strips the query
        # to mirror Router behavior, but the handler we pass in
        # gets ``path`` set to the full URL as the production
        # server does.
        handler = PostMockHandler(
            path="/api/media-integrity/reconcile?dry_run=1",
        )
        routes.handle_reconcile(handler)

        assert len(disp.calls) == 1
        forwarded_path = disp.calls[0][1]
        assert forwarded_path == (
            "/api/media-integrity/reconcile?dry_run=1"
        )


# ---------------------------------------------------------------------------
# /api/media-integrity/enforce-config
# ---------------------------------------------------------------------------


class TestEnforceConfigRoute:
    def test_forwards_body(self) -> None:
        disp = _StubDispatcher(response_body={"status": "enforced"})
        actor = _StubActorResolver()
        harness = _RouteHarness.with_routes(_routes_with(
            dispatcher=disp, actor=actor,
        ))
        response = _dispatch_post(
            harness, "/api/media-integrity/enforce-config",
            body={"target": "all"},
        )

        assert response.status == 200
        assert json.loads(response.body) == {"status": "enforced"}
        _, path, body, _ = disp.calls[0]
        assert path == "/api/media-integrity/enforce-config"
        assert body == {"target": "all"}


# ---------------------------------------------------------------------------
# /api/media-integrity/resolve-review
# ---------------------------------------------------------------------------


class TestResolveReviewRoute:
    def test_forwards_body(self) -> None:
        disp = _StubDispatcher(response_body={"resolved": True})
        actor = _StubActorResolver()
        harness = _RouteHarness.with_routes(_routes_with(
            dispatcher=disp, actor=actor,
        ))
        response = _dispatch_post(
            harness, "/api/media-integrity/resolve-review",
            body={
                "app": "radarr", "release_id": "abc",
                "winner_file_id": 7,
            },
        )

        assert response.status == 200
        _, path, body, _ = disp.calls[0]
        assert path == "/api/media-integrity/resolve-review"
        assert body == {
            "app": "radarr", "release_id": "abc", "winner_file_id": 7,
        }

    def test_400_for_missing_required_fields_via_dispatcher(self) -> None:
        """When the dispatcher (production
        ``_dispatch_media_integrity_via_job``) emits a 400 due
        to missing fields, the route layer forwards that
        verbatim — pin the boundary doesn't pre-empt
        validation."""
        def explode_dispatch(
            handler: Any, path: str, body: dict[str, Any], actor: Any,
        ) -> None:
            handler._json_response(400, {"error": "app is required"})

        actor = _StubActorResolver()
        routes = MediaIntegrityPostRoutes(
            mutation_gate=_AlwaysAllowGate(),
            dispatcher=_MediaIntegrityDispatcher(
                dispatch_fn=explode_dispatch,
            ),
            actor_resolver_provider=_ActorResolverProvider(
                resolver=actor,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/media-integrity/resolve-review",
            body={},
        )

        assert response.status == 400
        assert json.loads(response.body) == {"error": "app is required"}


# ---------------------------------------------------------------------------
# CSRF gate — security regression coverage
# ---------------------------------------------------------------------------


class TestCsrfGate:
    def test_reconcile_blocked_when_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        disp = _StubDispatcher()
        actor = _StubActorResolver()

        routes = MediaIntegrityPostRoutes(
            mutation_gate=gate,
            dispatcher=_MediaIntegrityDispatcher(
                dispatch_fn=disp.dispatch,
            ),
            actor_resolver_provider=_ActorResolverProvider(
                resolver=actor,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/media-integrity/reconcile",
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert disp.calls == []


# ---------------------------------------------------------------------------
# Provider default-path coverage
# ---------------------------------------------------------------------------


class TestProviderDefaultPath:
    def test_actor_resolver_default_path_uses_handlers_post_resolver(
        self, monkeypatch,
    ) -> None:
        from media_stack.api import handlers_post

        captured: dict[str, Any] = {}

        class _StubResolver:
            def resolve(
                self, handler: Any, body: dict[str, Any],
            ) -> Any:
                captured["body"] = body
                return "ACTOR"

        monkeypatch.setattr(
            handlers_post, "_actor_resolver", _StubResolver(),
        )
        provider = _ActorResolverProvider()
        assert provider.resolve("H", {"a": 1}) == "ACTOR"
        assert captured == {"body": {"a": 1}}

    def test_dispatcher_default_path_uses_handlers_post_helper(
        self, monkeypatch,
    ) -> None:
        from media_stack.api import handlers_post

        captured: dict[str, Any] = {}

        def stub_dispatch(handler, path, body, actor):
            captured["path"] = path
            captured["body"] = body
            captured["actor"] = actor

        monkeypatch.setattr(
            handlers_post,
            "_dispatch_media_integrity_via_job",
            stub_dispatch,
        )
        dispatcher = _MediaIntegrityDispatcher()
        dispatcher.dispatch("H", "/p", {"k": "v"}, "ACTOR")
        assert captured == {
            "path": "/p", "body": {"k": "v"}, "actor": "ACTOR",
        }


# ---------------------------------------------------------------------------
# Routing integration — auto-discovery + spec-parity
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    _EXPECTED = frozenset({
        "/api/media-integrity/reconcile",
        "/api/media-integrity/enforce-config",
        "/api/media-integrity/resolve-review",
    })

    def test_all_media_integrity_post_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST"
            and "MediaIntegrityPostRoutes" in r.display
        }
        assert registered == self._EXPECTED

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = MediaIntegrityPostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(
            instance._dispatcher, _MediaIntegrityDispatcher,
        )
        assert isinstance(
            instance._actor_resolver, _ActorResolverProvider,
        )
