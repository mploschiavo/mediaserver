"""Tests for ``api/routes/post_schedules_crud.py`` (ADR-0007 Phase 2 wave 8 group 2).

Covers the five schedule POST routes lifted off the legacy
``handlers_post`` elif chain. The legacy chain's behaviour is the
contract — these tests pin the wire shape (validation, error
envelopes, service-call wiring) at the route boundary so a later
refactor of ``services/scheduler.py`` doesn't accidentally regress
the dashboard's Schedules panel.

Test layout mirrors ``test_post_jobs_queue`` (same wave's template).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_schedules_crud import (
    ScheduleIdResolver,
    ScheduleRepository,
    ScheduleUpdateRequestParser,
    SchedulesPostRoutes,
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


# ---------------------------------------------------------------------------
# POST-aware mock handler — same shape as test_post_jobs_queue.
# ---------------------------------------------------------------------------


class PostMockHandler(MockControllerHandler):
    def __init__(
        self,
        *,
        path: str = "/",
        body: bytes | dict[str, Any] = b"",
        headers: dict[str, str] | None = None,
        state: Any = None,
    ) -> None:
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        merged_headers = dict(headers or {})
        if body and "Content-Length" not in merged_headers:
            merged_headers["Content-Length"] = str(len(body))
        super().__init__(
            path=path, body=body, headers=merged_headers, state=state,
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
        raise AssertionError(
            "_AlwaysAllowGate.reject called — verify should have "
            "returned True",
        )


# ---------------------------------------------------------------------------
# Harness — rebinds the auto-discovered SchedulesPostRoutes with a
# test-wired instance so stubs flow through the production Router.
# ---------------------------------------------------------------------------


class _RouteHarness:
    @classmethod
    def with_routes(
        cls, routes: SchedulesPostRoutes,
    ) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(
        cls, router: Router, routes: SchedulesPostRoutes,
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
        route: Any, routes: SchedulesPostRoutes,
    ) -> Any:
        if "SchedulesPostRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name)


def _dispatch_post(
    harness: RouteDispatchHarness,
    path: str,
    *,
    body: bytes | dict[str, Any] = b"",
    headers: dict[str, str] | None = None,
    state: Any = None,
) -> CapturedResponse:
    handler = PostMockHandler(
        path=path, body=body, headers=headers, state=state,
    )
    outcome = harness._dispatcher.try_dispatch("POST", path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        harness._dispatcher.write_method_not_allowed(handler, path)
    return handler.captured


def _routes_with(**kwargs: Any) -> SchedulesPostRoutes:
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    return SchedulesPostRoutes(**kwargs)


# ---------------------------------------------------------------------------
# /api/schedules — create
# ---------------------------------------------------------------------------


class TestCreateRoute:
    def test_forwards_full_body_to_repository(self) -> None:
        captured: dict[str, Any] = {}

        def fake_add(
            action: str, interval_seconds: int, label: str, enabled: bool,
        ) -> dict[str, Any]:
            captured["action"] = action
            captured["interval_seconds"] = interval_seconds
            captured["label"] = label
            captured["enabled"] = enabled
            return {
                "status": "created",
                "schedule": {
                    "id": 99, "action": action,
                    "interval_seconds": interval_seconds,
                    "label": label, "enabled": enabled,
                },
            }

        routes = _routes_with(
            repository=ScheduleRepository(add_fn=fake_add),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/schedules",
            body={
                "action": "media-integrity:reconcile",
                "interval_seconds": 3600,
                "label": "hourly reconcile",
                "enabled": True,
            },
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "created"
        assert body["schedule"]["id"] == 99
        assert captured == {
            "action": "media-integrity:reconcile",
            "interval_seconds": 3600,
            "label": "hourly reconcile",
            "enabled": True,
        }

    def test_defaults_when_body_omits_optional_fields(self) -> None:
        captured: dict[str, Any] = {}

        def fake_add(
            action: str, interval_seconds: int, label: str, enabled: bool,
        ) -> dict[str, Any]:
            captured["action"] = action
            captured["interval_seconds"] = interval_seconds
            captured["label"] = label
            captured["enabled"] = enabled
            return {"status": "created"}

        routes = _routes_with(
            repository=ScheduleRepository(add_fn=fake_add),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/api/schedules",
            body={"action": "envoy-config-rebuild", "interval_seconds": 60},
        )

        # Empty/missing label collapses to "" — same semantic the
        # legacy chain enforced.
        assert captured["label"] == ""
        # Default ``enabled`` is True.
        assert captured["enabled"] is True

    def test_validation_error_passes_through(self) -> None:
        """Service-layer validation errors flow back as the
        ``{error: ...}`` envelope."""
        routes = _routes_with(
            repository=ScheduleRepository(
                add_fn=lambda *a, **k: {"error": "interval too small"},
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/schedules",
            body={"action": "x", "interval_seconds": 5},
        )

        assert response.status == 200
        assert json.loads(response.body) == {"error": "interval too small"}


# ---------------------------------------------------------------------------
# /api/schedules/{schedule_id}/delete
# ---------------------------------------------------------------------------


class TestDeleteRoute:
    def test_valid_id_forwards_to_remove(self) -> None:
        captured: dict[str, Any] = {}

        def fake_remove(schedule_id: int) -> dict[str, Any]:
            captured["schedule_id"] = schedule_id
            return {"status": "removed", "schedule_id": schedule_id}

        routes = _routes_with(
            repository=ScheduleRepository(remove_fn=fake_remove),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/schedules/12345/delete",
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "status": "removed", "schedule_id": 12345,
        }
        assert isinstance(captured["schedule_id"], int)

    def test_non_integer_id_returns_400(self) -> None:
        routes = _routes_with(
            repository=ScheduleRepository(
                remove_fn=lambda sid: pytest.fail(
                    "should not call remove",
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/schedules/not-an-int/delete",
        )

        assert response.status == 400
        assert json.loads(response.body) == {"error": "Invalid schedule ID"}

    def test_unknown_id_returns_service_error_envelope(self) -> None:
        routes = _routes_with(
            repository=ScheduleRepository(
                remove_fn=lambda sid: {
                    "error": f"Schedule {sid} not found",
                },
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/schedules/9999/delete")

        assert response.status == 200
        assert json.loads(response.body) == {
            "error": "Schedule 9999 not found",
        }


# ---------------------------------------------------------------------------
# /api/schedules/{schedule_id}/pause | /resume
# ---------------------------------------------------------------------------


class TestPauseResumeRoutes:
    def test_pause_forwards_enabled_false(self) -> None:
        captured: dict[str, Any] = {}

        def fake_set(schedule_id: int, *, enabled: bool) -> dict[str, Any]:
            captured["schedule_id"] = schedule_id
            captured["enabled"] = enabled
            return {"status": "updated"}

        routes = _routes_with(
            repository=ScheduleRepository(set_enabled_fn=fake_set),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/schedules/42/pause")

        assert response.status == 200
        assert captured == {"schedule_id": 42, "enabled": False}

    def test_resume_forwards_enabled_true(self) -> None:
        captured: dict[str, Any] = {}

        def fake_set(schedule_id: int, *, enabled: bool) -> dict[str, Any]:
            captured["schedule_id"] = schedule_id
            captured["enabled"] = enabled
            return {"status": "updated"}

        routes = _routes_with(
            repository=ScheduleRepository(set_enabled_fn=fake_set),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/schedules/42/resume")

        assert response.status == 200
        assert captured == {"schedule_id": 42, "enabled": True}

    def test_pause_non_integer_id_returns_400(self) -> None:
        routes = _routes_with(
            repository=ScheduleRepository(
                set_enabled_fn=lambda sid, **_: pytest.fail(
                    "should not call set_enabled",
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/schedules/abc/pause")

        assert response.status == 400


# ---------------------------------------------------------------------------
# /api/schedules/{schedule_id}/update
# ---------------------------------------------------------------------------


class TestUpdateRoute:
    def test_forwards_only_supplied_keys(self) -> None:
        captured: dict[str, Any] = {}

        def fake_update(
            schedule_id: int, **kwargs: Any,
        ) -> dict[str, Any]:
            captured["schedule_id"] = schedule_id
            captured["kwargs"] = kwargs
            return {"status": "updated"}

        routes = _routes_with(
            repository=ScheduleRepository(update_fn=fake_update),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/api/schedules/42/update",
            body={"label": "renamed"},
        )

        # ``label`` is supplied; nothing else should leak through.
        assert captured == {
            "schedule_id": 42, "kwargs": {"label": "renamed"},
        }

    def test_full_update_forwards_all_keys(self) -> None:
        captured: dict[str, Any] = {}

        def fake_update(
            schedule_id: int, **kwargs: Any,
        ) -> dict[str, Any]:
            captured["kwargs"] = kwargs
            return {"status": "updated"}

        routes = _routes_with(
            repository=ScheduleRepository(update_fn=fake_update),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/api/schedules/42/update",
            body={
                "action": "test:job",
                "interval_seconds": "120",
                "label": "x",
                "enabled": False,
            },
        )

        assert captured["kwargs"] == {
            "action": "test:job",
            "interval_seconds": 120,
            "label": "x",
            "enabled": False,
        }

    def test_non_integer_id_returns_400(self) -> None:
        routes = _routes_with(
            repository=ScheduleRepository(
                update_fn=lambda sid, **_: pytest.fail(
                    "should not call update",
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/schedules/abc/update", body={"label": "x"},
        )

        assert response.status == 400


# ---------------------------------------------------------------------------
# Strategy unit coverage
# ---------------------------------------------------------------------------


class TestScheduleIdResolver:
    def test_int_string_parses(self) -> None:
        parsed, error = ScheduleIdResolver().parse("42")
        assert parsed == 42
        assert error is None

    def test_real_int_parses(self) -> None:
        parsed, error = ScheduleIdResolver().parse(7)
        assert parsed == 7
        assert error is None

    def test_non_numeric_returns_error_envelope(self) -> None:
        parsed, error = ScheduleIdResolver().parse("abc")
        assert parsed is None
        assert error == {"error": "Invalid schedule ID"}

    def test_none_returns_error_envelope(self) -> None:
        parsed, error = ScheduleIdResolver().parse(None)
        assert parsed is None
        assert error == {"error": "Invalid schedule ID"}


class TestScheduleUpdateRequestParser:
    def test_empty_body_yields_empty_kwargs(self) -> None:
        assert ScheduleUpdateRequestParser().build({}) == {}

    def test_only_supplied_keys_forwarded(self) -> None:
        assert ScheduleUpdateRequestParser().build(
            {"action": "x"},
        ) == {"action": "x"}

    def test_interval_coerced_to_int(self) -> None:
        assert ScheduleUpdateRequestParser().build(
            {"interval_seconds": "120"},
        ) == {"interval_seconds": 120}

    def test_interval_passthrough_when_uncoercible(self) -> None:
        result = ScheduleUpdateRequestParser().build(
            {"interval_seconds": "not-a-number"},
        )
        assert result == {"interval_seconds": "not-a-number"}

    def test_enabled_coerced_to_bool(self) -> None:
        # Truthy non-bool should land as bool.
        assert ScheduleUpdateRequestParser().build(
            {"enabled": 1},
        ) == {"enabled": True}
        assert ScheduleUpdateRequestParser().build(
            {"enabled": 0},
        ) == {"enabled": False}


class TestRepositoryDefaultPath:
    """Pin that ``ScheduleRepository`` does a fresh module-attribute
    lookup on each call when no constructor-injected callable is
    supplied — avoids the lazy-cache resolver shape that earlier
    waves had to retro-clean.
    """

    def test_add_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import scheduler as sched_svc

        calls: list[Any] = []

        def stub_add(
            action: str, interval_seconds: int, label: str, enabled: bool,
        ) -> dict[str, Any]:
            calls.append((action, interval_seconds, label, enabled))
            return {"status": "created"}

        monkeypatch.setattr(sched_svc, "add_schedule", stub_add)
        repo = ScheduleRepository()
        repo.add("test", 60, "x", enabled=True)
        assert calls == [("test", 60, "x", True)]

    def test_remove_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import scheduler as sched_svc

        monkeypatch.setattr(
            sched_svc, "remove_schedule",
            lambda sid: {"status": "removed", "schedule_id": sid},
        )
        repo = ScheduleRepository()
        assert repo.remove(7) == {
            "status": "removed", "schedule_id": 7,
        }

    def test_set_enabled_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import scheduler as sched_svc

        captured: dict[str, Any] = {}

        def stub_set(sid: int, *, enabled: bool) -> dict[str, Any]:
            captured["sid"] = sid
            captured["enabled"] = enabled
            return {"status": "updated"}

        monkeypatch.setattr(
            sched_svc, "set_schedule_enabled", stub_set,
        )
        repo = ScheduleRepository()
        repo.set_enabled(11, enabled=False)
        assert captured == {"sid": 11, "enabled": False}

    def test_update_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import scheduler as sched_svc

        captured: dict[str, Any] = {}

        def stub_update(sid: int, **kwargs: Any) -> dict[str, Any]:
            captured["sid"] = sid
            captured["kwargs"] = kwargs
            return {"status": "updated"}

        monkeypatch.setattr(sched_svc, "update_schedule", stub_update)
        repo = ScheduleRepository()
        repo.update(11, label="x")
        assert captured == {"sid": 11, "kwargs": {"label": "x"}}


# ---------------------------------------------------------------------------
# CSRF gate — security regression coverage
# ---------------------------------------------------------------------------


class TestCsrfGate:
    """Pin the CSRF double-submit semantics for the migrated
    schedule routes. The legacy chain enforced this in
    ``_global_preflight``; the Router-dispatched path bypasses
    that, so the gate is the only line of defence — regressions
    here would re-open the CSRF surface for schedule mutations.
    """

    def test_create_blocked_when_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        add_calls: list[Any] = []

        routes = SchedulesPostRoutes(
            mutation_gate=gate,
            repository=ScheduleRepository(
                add_fn=lambda *a, **k: (
                    add_calls.append((a, k)) or {"status": "created"}
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/schedules",
            body={"action": "x", "interval_seconds": 60},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert add_calls == []

    def test_delete_blocked_when_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        remove_calls: list[Any] = []

        routes = SchedulesPostRoutes(
            mutation_gate=gate,
            repository=ScheduleRepository(
                remove_fn=lambda sid: (
                    remove_calls.append(sid) or {"status": "removed"}
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/schedules/42/delete",
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert remove_calls == []


# ---------------------------------------------------------------------------
# Routing integration — auto-discovery + spec-parity
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    _EXPECTED = frozenset({
        "/api/schedules",
        "/api/schedules/{schedule_id}/delete",
        "/api/schedules/{schedule_id}/pause",
        "/api/schedules/{schedule_id}/resume",
        "/api/schedules/{schedule_id}/update",
    })

    def test_all_schedule_post_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST"
            and "SchedulesPostRoutes" in r.display
        }
        assert registered == self._EXPECTED

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = SchedulesPostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(instance._repo, ScheduleRepository)
        assert isinstance(instance._id_resolver, ScheduleIdResolver)
        assert isinstance(
            instance._update_parser, ScheduleUpdateRequestParser,
        )
