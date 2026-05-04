"""Tests for ``api/routes/post_indexers_import_lists.py`` (ADR-0007 Phase 2 wave 8 group 2).

Covers the four indexer + import-list POST routes lifted off the
legacy ``handlers_post`` elif chain. Tests pin the wire shape
(validation, error envelopes, service-call wiring) at the route
boundary so a later refactor of ``services/content.py`` doesn't
regress the dashboard's Indexers / Import-Lists panels.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_indexers_import_lists import (
    ContentService,
    IndexersImportListsPostRoutes,
    IntIdResolver,
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


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _RouteHarness:
    @classmethod
    def with_routes(
        cls, routes: IndexersImportListsPostRoutes,
    ) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(
        cls, router: Router, routes: IndexersImportListsPostRoutes,
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
        route: Any, routes: IndexersImportListsPostRoutes,
    ) -> Any:
        if "IndexersImportListsPostRoutes" not in route.display:
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
    outcome = harness._dispatcher.try_dispatch("POST", path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        harness._dispatcher.write_method_not_allowed(handler, path)
    return handler.captured


def _routes_with(**kwargs: Any) -> IndexersImportListsPostRoutes:
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    return IndexersImportListsPostRoutes(**kwargs)


# ---------------------------------------------------------------------------
# /api/indexers/{indexer_id} — DELETE-via-POST tunnel
# ---------------------------------------------------------------------------


class TestIndexerDeleteTunnelRoute:
    def test_delete_tunnel_forwards_to_delete_indexer(self) -> None:
        captured: dict[str, Any] = {}

        def fake_delete(indexer_id: int) -> dict[str, Any]:
            captured["indexer_id"] = indexer_id
            return {"status": "deleted", "indexer_id": indexer_id}

        routes = _routes_with(content_service=ContentService(
            delete_indexer_fn=fake_delete,
        ))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/indexers/42",
            body={"_method": "DELETE"},
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "status": "deleted", "indexer_id": 42,
        }
        assert captured == {"indexer_id": 42}

    def test_missing_method_field_returns_404(self) -> None:
        """Without ``{"_method": "DELETE"}`` the legacy chain
        falls through to the 404 sink — pin we mirror that."""
        delete_calls: list[Any] = []

        routes = _routes_with(content_service=ContentService(
            delete_indexer_fn=lambda iid: (
                delete_calls.append(iid) or {}
            ),
        ))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/indexers/42", body={},
        )

        assert response.status == 404
        assert delete_calls == []

    def test_non_integer_id_returns_400(self) -> None:
        routes = _routes_with(content_service=ContentService(
            delete_indexer_fn=lambda iid: pytest.fail(
                "should not call delete_indexer",
            ),
        ))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/indexers/not-an-int",
            body={"_method": "DELETE"},
        )

        assert response.status == 400
        assert json.loads(response.body) == {
            "error": "Invalid indexer ID",
        }


# ---------------------------------------------------------------------------
# /api/indexers/{indexer_id}/toggle
# ---------------------------------------------------------------------------


class TestIndexerToggleRoute:
    def test_enable_true_default(self) -> None:
        captured: dict[str, Any] = {}

        def fake_toggle(
            indexer_id: int, enable: bool,
        ) -> dict[str, Any]:
            captured["indexer_id"] = indexer_id
            captured["enable"] = enable
            return {"status": "ok", "enable": enable}

        routes = _routes_with(content_service=ContentService(
            toggle_indexer_fn=fake_toggle,
        ))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/indexers/42/toggle", body={},
        )

        assert response.status == 200
        # Body without ``enable`` defaults to True (legacy semantic).
        assert captured == {"indexer_id": 42, "enable": True}

    def test_enable_false_explicit(self) -> None:
        captured: dict[str, Any] = {}

        def fake_toggle(
            indexer_id: int, enable: bool,
        ) -> dict[str, Any]:
            captured["enable"] = enable
            return {"status": "ok"}

        routes = _routes_with(content_service=ContentService(
            toggle_indexer_fn=fake_toggle,
        ))
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/api/indexers/42/toggle", body={"enable": False},
        )

        assert captured == {"enable": False}

    def test_non_integer_id_returns_400(self) -> None:
        routes = _routes_with(content_service=ContentService(
            toggle_indexer_fn=lambda *_: pytest.fail("should not call"),
        ))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/indexers/abc/toggle", body={},
        )

        assert response.status == 400


# ---------------------------------------------------------------------------
# /api/import-lists/{service}/{list_id}/delete
# ---------------------------------------------------------------------------


class TestImportListDeleteRoute:
    def test_forwards_service_and_list_id(self) -> None:
        captured: dict[str, Any] = {}

        def fake_delete(
            service_id: str, list_id: int,
        ) -> dict[str, Any]:
            captured["service_id"] = service_id
            captured["list_id"] = list_id
            return {
                "status": "deleted",
                "service": service_id, "list_id": list_id,
            }

        routes = _routes_with(content_service=ContentService(
            delete_import_list_fn=fake_delete,
        ))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/import-lists/radarr/7/delete",
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "status": "deleted", "service": "radarr", "list_id": 7,
        }
        assert captured == {"service_id": "radarr", "list_id": 7}

    def test_non_integer_list_id_returns_400(self) -> None:
        routes = _routes_with(content_service=ContentService(
            delete_import_list_fn=lambda *_: pytest.fail(
                "should not call",
            ),
        ))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/import-lists/radarr/abc/delete",
        )

        assert response.status == 400
        assert json.loads(response.body) == {
            "error": "Invalid list ID",
        }


# ---------------------------------------------------------------------------
# /api/import-lists/{service}/{list_id}/toggle
# ---------------------------------------------------------------------------


class TestImportListToggleRoute:
    def test_enabled_true_default(self) -> None:
        captured: dict[str, Any] = {}

        def fake_toggle(
            service_id: str, list_id: int, enabled: bool,
        ) -> dict[str, Any]:
            captured["service_id"] = service_id
            captured["list_id"] = list_id
            captured["enabled"] = enabled
            return {"status": "toggled", "enabled": enabled}

        routes = _routes_with(content_service=ContentService(
            toggle_import_list_fn=fake_toggle,
        ))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/import-lists/sonarr/3/toggle",
            body={},
        )

        assert response.status == 200
        # Default ``enabled`` is True when body omits it.
        assert captured == {
            "service_id": "sonarr", "list_id": 3, "enabled": True,
        }

    def test_enabled_false_explicit(self) -> None:
        captured: dict[str, Any] = {}

        def fake_toggle(
            service_id: str, list_id: int, enabled: bool,
        ) -> dict[str, Any]:
            captured["enabled"] = enabled
            return {"status": "toggled"}

        routes = _routes_with(content_service=ContentService(
            toggle_import_list_fn=fake_toggle,
        ))
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/api/import-lists/sonarr/3/toggle",
            body={"enabled": False},
        )

        assert captured == {"enabled": False}

    def test_non_integer_list_id_returns_400(self) -> None:
        routes = _routes_with(content_service=ContentService(
            toggle_import_list_fn=lambda *_: pytest.fail(
                "should not call",
            ),
        ))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/import-lists/sonarr/abc/toggle",
            body={},
        )

        assert response.status == 400


# ---------------------------------------------------------------------------
# Strategy unit coverage — IntIdResolver
# ---------------------------------------------------------------------------


class TestIntIdResolver:
    def test_int_string_parses(self) -> None:
        parsed, error = IntIdResolver(label="thing").parse("42")
        assert parsed == 42
        assert error is None

    def test_real_int_parses(self) -> None:
        parsed, error = IntIdResolver(label="thing").parse(7)
        assert parsed == 7
        assert error is None

    def test_non_numeric_label_in_error(self) -> None:
        parsed, error = IntIdResolver(label="indexer ID").parse("xyz")
        assert parsed is None
        assert error == {"error": "Invalid indexer ID"}

    def test_distinct_label_changes_message(self) -> None:
        _, error = IntIdResolver(label="list ID").parse("xyz")
        assert error == {"error": "Invalid list ID"}


# ---------------------------------------------------------------------------
# Default-path coverage — fresh module attribute lookup
# ---------------------------------------------------------------------------


class TestContentServiceDefaultPath:
    def test_toggle_indexer_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import content as content_svc

        captured: dict[str, Any] = {}

        def stub(indexer_id: int, enable: bool) -> dict[str, Any]:
            captured["iid"] = indexer_id
            captured["enable"] = enable
            return {"status": "ok"}

        monkeypatch.setattr(content_svc, "toggle_indexer", stub)
        svc = ContentService()
        result = svc.toggle_indexer(42, True)
        assert result == {"status": "ok"}
        assert captured == {"iid": 42, "enable": True}

    def test_delete_indexer_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import content as content_svc

        monkeypatch.setattr(
            content_svc, "delete_indexer",
            lambda iid: {"status": "deleted", "indexer_id": iid},
        )
        svc = ContentService()
        assert svc.delete_indexer(7) == {
            "status": "deleted", "indexer_id": 7,
        }

    def test_toggle_import_list_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import content as content_svc

        captured: dict[str, Any] = {}

        def stub(svc_id: str, list_id: int, enabled: bool) -> dict[str, Any]:
            captured["svc_id"] = svc_id
            captured["list_id"] = list_id
            captured["enabled"] = enabled
            return {"status": "toggled"}

        monkeypatch.setattr(content_svc, "toggle_import_list", stub)
        svc = ContentService()
        svc.toggle_import_list("radarr", 3, False)
        assert captured == {
            "svc_id": "radarr", "list_id": 3, "enabled": False,
        }

    def test_delete_import_list_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import content as content_svc

        captured: dict[str, Any] = {}

        def stub(svc_id: str, list_id: int) -> dict[str, Any]:
            captured["svc_id"] = svc_id
            captured["list_id"] = list_id
            return {"status": "deleted"}

        monkeypatch.setattr(content_svc, "delete_import_list", stub)
        svc = ContentService()
        svc.delete_import_list("sonarr", 5)
        assert captured == {"svc_id": "sonarr", "list_id": 5}


# ---------------------------------------------------------------------------
# CSRF gate
# ---------------------------------------------------------------------------


class TestCsrfGate:
    def test_indexer_toggle_blocked_when_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        toggle_calls: list[Any] = []

        routes = IndexersImportListsPostRoutes(
            mutation_gate=gate,
            content_service=ContentService(
                toggle_indexer_fn=lambda iid, en: (
                    toggle_calls.append((iid, en)) or {}
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/indexers/42/toggle",
            body={"enable": True},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert toggle_calls == []

    def test_import_list_delete_blocked_when_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        delete_calls: list[Any] = []

        routes = IndexersImportListsPostRoutes(
            mutation_gate=gate,
            content_service=ContentService(
                delete_import_list_fn=lambda *a: (
                    delete_calls.append(a) or {}
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/import-lists/radarr/7/delete",
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert delete_calls == []


# ---------------------------------------------------------------------------
# Routing integration — auto-discovery + spec-parity
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    _EXPECTED = frozenset({
        "/api/indexers/{indexer_id}",
        "/api/indexers/{indexer_id}/toggle",
        "/api/import-lists/{service}/{list_id}/delete",
        "/api/import-lists/{service}/{list_id}/toggle",
    })

    def test_all_indexer_import_list_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST"
            and "IndexersImportListsPostRoutes" in r.display
        }
        assert registered == self._EXPECTED

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = IndexersImportListsPostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(instance._content, ContentService)
        assert isinstance(instance._indexer_id_resolver, IntIdResolver)
        assert isinstance(instance._list_id_resolver, IntIdResolver)
