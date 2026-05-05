"""Tests for ``api/routes/disk_guardrails.py`` (ADR-0008 Phase 2).

Mirrors the wave-5 ``test_post_admin_ops.py`` shape:

* ``_RouteHarness`` rebinds the auto-discovered ``DiskGuardrailsRoutes``
  with a test-wired instance so collaborators are stubs without
  monkey-patching.
* ``_AlwaysAllowGate`` lets business-logic tests bypass CSRF; one
  dedicated test class exercises the real ``PostMutationGate`` path
  (CSRF rejection + admin-role rejection) so security regressions
  surface clearly.
* ``PostMockHandler`` adds the ``_read_json_body`` shape the production
  handler exposes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from media_stack.api.routes.disk_guardrails import (
    ActorResolver,
    AdminGate,
    AuditAppender,
    CleanupRunner,
    DiskGuardrailsRoutes,
    HoursQueryParser,
    TransitionFeedReader,
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
# POST-aware mock handler (mirrors ``test_post_admin_ops.PostMockHandler``)
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
        raise AssertionError("reject called — verify returned True")


class _AlwaysAdminGate(AdminGate):
    """``AdminGate`` substitute that admits every caller."""

    def __init__(self) -> None:  # noqa: D401 — no super needed
        super().__init__()

    def is_admin(self, username: str) -> bool:  # noqa: ARG002
        return True


class _AlwaysDenyAdminGate(AdminGate):
    def __init__(self) -> None:
        super().__init__()

    def is_admin(self, username: str) -> bool:  # noqa: ARG002
        return False


class _StubLockdown:
    """Captures the calls the route makes against the lockdown
    service and returns deterministic shapes."""

    def __init__(
        self,
        *,
        state: dict[str, Any] | None = None,
        engage_result: dict[str, Any] | None = None,
        release_result: dict[str, Any] | None = None,
        pause_auto_result: dict[str, Any] | None = None,
    ) -> None:
        self._state = dict(state or {})
        self._engage_result = engage_result or {
            "paused_clients": ["qbittorrent", "sabnzbd"],
            "failures": [],
            "engaged": True,
            "trigger": "manual",
            "already_engaged": False,
        }
        self._release_result = release_result or {
            "released_clients": ["qbittorrent", "sabnzbd"],
            "failures": [],
            "engaged": False,
            "was_engaged": True,
        }
        self._pause_auto_result = pause_auto_result or {
            "auto_check_paused_until": 1_750_000_000.0,
            "hours": 1,
            "by": "operator:test",
        }
        self.engage_calls: list[dict[str, Any]] = []
        self.release_calls: list[dict[str, Any]] = []
        self.pause_auto_calls: list[dict[str, Any]] = []

    def get_state(self) -> dict[str, Any]:
        return dict(self._state)

    def engage(self, *, trigger: str, by: str) -> dict[str, Any]:
        self.engage_calls.append({"trigger": trigger, "by": by})
        return dict(self._engage_result)

    def release(self, *, by: str) -> dict[str, Any]:
        self.release_calls.append({"by": by})
        return dict(self._release_result)

    def pause_auto(self, *, hours: int, by: str) -> dict[str, Any]:
        self.pause_auto_calls.append({"hours": hours, "by": by})
        return dict(self._pause_auto_result)


class _StubAudit(AuditAppender):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, Any]] = []

    def append(self, *, actor: str, action: str, result: str, detail: dict[str, Any]) -> None:
        self.rows.append({
            "actor": actor,
            "action": action,
            "result": result,
            "detail": dict(detail or {}),
        })


class _StubTransitions(TransitionFeedReader):
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self._rows = rows or []

    def recent(self) -> list[dict[str, Any]]:
        return list(self._rows)


# ---------------------------------------------------------------------------
# Harness — replaces the auto-discovered DiskGuardrailsRoutes
# ---------------------------------------------------------------------------


class _RouteHarness:
    @classmethod
    def with_routes(
        cls, routes: DiskGuardrailsRoutes,
    ) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Router, routes: DiskGuardrailsRoutes) -> None:
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
    def _maybe_replacement(route: Any, routes: DiskGuardrailsRoutes) -> Any:
        if "DiskGuardrailsRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name)


def _routes_with(**kwargs: Any) -> DiskGuardrailsRoutes:
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    kwargs.setdefault("admin_gate", _AlwaysAdminGate())
    kwargs.setdefault("audit_appender", _StubAudit())
    return DiskGuardrailsRoutes(**kwargs)


def _dispatch(
    harness: RouteDispatchHarness,
    verb: str,
    path: str,
    *,
    body: bytes | dict[str, Any] = b"",
    headers: dict[str, str] | None = None,
    state: Any = None,
) -> CapturedResponse:
    """Dispatch a request through the harness. ``path`` may carry a
    query string (e.g. ``"/x?hours=1"``); the routing layer is
    contract-bound to receive the raw path component, but the
    handler needs the full path on ``self.path`` so the route's
    ``HoursQueryParser`` can see the query string."""
    handler = PostMockHandler(
        path=path, body=body, headers=headers, state=state,
    )
    routing_path = path.split("?", 1)[0]
    outcome = harness._dispatcher.try_dispatch(verb, routing_path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        harness._dispatcher.write_method_not_allowed(handler, routing_path)
    return handler.captured


class _StubRegistry:
    def __init__(
        self,
        threshold: dict[str, Any] | None = None,
    ) -> None:
        self._threshold = threshold or {
            "lockdown_percent": 75.0,
            "release_percent": 60.0,
        }

    def threshold_for(self, rule_id: str) -> dict[str, Any]:  # noqa: ARG002
        return dict(self._threshold)


# ---------------------------------------------------------------------------
# GET /api/disk-guardrails — handle_status
# ---------------------------------------------------------------------------


class TestStatusRoute:
    def test_no_lockdown_returns_normal(self) -> None:
        lockdown = _StubLockdown(state={
            "engaged": False, "trigger": None,
            "engaged_at": 0.0, "engaged_by": "",
            "auto_check_paused_until": None,
            "paused_clients": [], "last_failures": [],
        })
        routes = _routes_with(
            lockdown_service=lockdown,
            registry_provider=lambda: _StubRegistry(),
            transition_reader=_StubTransitions(),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "GET", "/api/disk-guardrails")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["state"] == "NORMAL"
        assert body["thresholds"] == {
            "lockdown_percent": 75.0, "release_percent": 60.0,
        }
        assert body["paused_clients"] == []
        assert body["transitions"] == []

    def test_manual_lockdown_returns_manual_state(self) -> None:
        lockdown = _StubLockdown(state={
            "engaged": True, "trigger": "manual",
            "engaged_at": 1_700_000_000.0,
            "engaged_by": "operator:matthew",
            "auto_check_paused_until": None,
            "paused_clients": ["qbittorrent", "sabnzbd"],
            "last_failures": [],
        })
        routes = _routes_with(
            lockdown_service=lockdown,
            registry_provider=lambda: _StubRegistry(),
            transition_reader=_StubTransitions(),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "GET", "/api/disk-guardrails")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["state"] == "MANUAL_LOCKDOWN"
        assert body["paused_clients"] == ["qbittorrent", "sabnzbd"]
        assert body["engaged_by"] == "operator:matthew"

    def test_auto_lockdown_returns_auto_state(self) -> None:
        lockdown = _StubLockdown(state={
            "engaged": True, "trigger": "auto",
            "engaged_at": 1_700_000_000.0,
            "engaged_by": "auto:disk-78%",
            "auto_check_paused_until": None,
            "paused_clients": ["qbittorrent"],
            "last_failures": [],
        })
        routes = _routes_with(
            lockdown_service=lockdown,
            registry_provider=lambda: _StubRegistry(),
            transition_reader=_StubTransitions(),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "GET", "/api/disk-guardrails")
        body = json.loads(response.body)
        assert body["state"] == "AUTO_LOCKDOWN"


# ---------------------------------------------------------------------------
# POST /api/disk-guardrails/cleanup — handle_cleanup
# ---------------------------------------------------------------------------


class _StubCleanup(CleanupRunner):
    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__()
        self._report = report
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        *,
        categories_override: list[str] | None,
        max_delete_override: int | None,
        force: bool,
    ) -> dict[str, Any]:
        self.calls.append({
            "categories_override": categories_override,
            "max_delete_override": max_delete_override,
            "force": force,
        })
        return dict(self._report)


class TestCleanupRoute:
    def test_synchronous_run_returns_report(self) -> None:
        cleanup = _StubCleanup({
            "deleted": 14, "freed_gb": 32.5,
            "kept": 2, "candidates_evaluated": 16,
            "strategy": "oldest_first",
        })
        audit = _StubAudit()
        routes = _routes_with(cleanup_runner=cleanup, audit_appender=audit)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "POST", "/api/disk-guardrails/cleanup")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["deleted"] == 14
        assert body["freed_gb"] == 32.5
        assert cleanup.calls[0]["force"] is True
        assert cleanup.calls[0]["categories_override"] is None
        assert cleanup.calls[0]["max_delete_override"] is None
        # Audit row written.
        assert any(
            r["action"] == "disk_guardrail_cleanup_invoked"
            for r in audit.rows
        )

    def test_categories_override_forwarded(self) -> None:
        cleanup = _StubCleanup({
            "deleted": 0, "freed_gb": 0.0, "kept": 0,
            "candidates_evaluated": 0, "strategy": "oldest_first",
        })
        routes = _routes_with(cleanup_runner=cleanup)
        harness = _RouteHarness.with_routes(routes)
        _dispatch(
            harness, "POST", "/api/disk-guardrails/cleanup",
            body={"categories": ["tv-sonarr", "movies-radarr"]},
        )
        assert cleanup.calls[0]["categories_override"] == [
            "tv-sonarr", "movies-radarr",
        ]

    def test_max_delete_override_forwarded(self) -> None:
        cleanup = _StubCleanup({
            "deleted": 0, "freed_gb": 0.0, "kept": 0,
            "candidates_evaluated": 0, "strategy": "oldest_first",
        })
        routes = _routes_with(cleanup_runner=cleanup)
        harness = _RouteHarness.with_routes(routes)
        _dispatch(
            harness, "POST", "/api/disk-guardrails/cleanup",
            body={"max_delete": 5},
        )
        assert cleanup.calls[0]["max_delete_override"] == 5

    def test_invalid_categories_returns_400(self) -> None:
        cleanup = _StubCleanup({"deleted": 0, "freed_gb": 0.0, "kept": 0,
                                "candidates_evaluated": 0,
                                "strategy": "oldest_first"})
        routes = _routes_with(cleanup_runner=cleanup)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(
            harness, "POST", "/api/disk-guardrails/cleanup",
            body={"categories": "not-a-list"},
        )
        assert response.status == 400

    def test_invalid_max_delete_returns_400(self) -> None:
        cleanup = _StubCleanup({"deleted": 0, "freed_gb": 0.0, "kept": 0,
                                "candidates_evaluated": 0,
                                "strategy": "oldest_first"})
        routes = _routes_with(cleanup_runner=cleanup)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(
            harness, "POST", "/api/disk-guardrails/cleanup",
            body={"max_delete": "not-an-int"},
        )
        assert response.status == 400


# ---------------------------------------------------------------------------
# POST /api/disk-guardrails/lockdown — handle_lockdown
# ---------------------------------------------------------------------------


class TestLockdownRoute:
    def test_engages_with_manual_trigger(self) -> None:
        lockdown = _StubLockdown()
        audit = _StubAudit()
        routes = _routes_with(
            lockdown_service=lockdown, audit_appender=audit,
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "POST", "/api/disk-guardrails/lockdown")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["state"] == "MANUAL_LOCKDOWN"
        assert lockdown.engage_calls[0]["trigger"] == "manual"
        assert lockdown.engage_calls[0]["by"].startswith("operator:")
        assert any(
            r["action"] == "disk_guardrail_lockdown_engaged"
            for r in audit.rows
        )

    def test_engage_failure_returns_500(self) -> None:
        class _FailLockdown(_StubLockdown):
            def engage(self, *, trigger: str, by: str) -> dict[str, Any]:  # noqa: ARG002
                raise RuntimeError("boom")
        routes = _routes_with(lockdown_service=_FailLockdown())
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "POST", "/api/disk-guardrails/lockdown")
        assert response.status == 500


# ---------------------------------------------------------------------------
# POST /api/disk-guardrails/release — handle_release
# ---------------------------------------------------------------------------


class TestReleaseRoute:
    def test_releases_to_normal_state(self) -> None:
        lockdown = _StubLockdown()
        audit = _StubAudit()
        routes = _routes_with(
            lockdown_service=lockdown, audit_appender=audit,
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "POST", "/api/disk-guardrails/release")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["state"] == "NORMAL"
        assert body["released_clients"] == ["qbittorrent", "sabnzbd"]
        assert any(
            r["action"] == "disk_guardrail_lockdown_released"
            for r in audit.rows
        )


# ---------------------------------------------------------------------------
# POST /api/disk-guardrails/pause-auto — handle_pause_auto
# ---------------------------------------------------------------------------


class TestPauseAutoRoute:
    def test_hours_one_sets_ttl(self) -> None:
        lockdown = _StubLockdown()
        routes = _routes_with(lockdown_service=lockdown)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(
            harness, "POST", "/api/disk-guardrails/pause-auto?hours=1",
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["hours"] == 1
        assert body["paused_until"] is not None
        assert lockdown.pause_auto_calls[0]["hours"] == 1

    def test_hours_zero_rejected_with_400(self) -> None:
        lockdown = _StubLockdown()
        routes = _routes_with(lockdown_service=lockdown)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(
            harness, "POST", "/api/disk-guardrails/pause-auto?hours=0",
        )
        assert response.status == 400
        assert lockdown.pause_auto_calls == []

    def test_hours_25_clamped_to_24(self) -> None:
        lockdown = _StubLockdown()
        routes = _routes_with(lockdown_service=lockdown)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(
            harness, "POST", "/api/disk-guardrails/pause-auto?hours=25",
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["hours"] == 24
        assert lockdown.pause_auto_calls[0]["hours"] == 24

    def test_missing_hours_returns_400(self) -> None:
        lockdown = _StubLockdown()
        routes = _routes_with(lockdown_service=lockdown)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(
            harness, "POST", "/api/disk-guardrails/pause-auto",
        )
        assert response.status == 400

    def test_non_integer_hours_returns_400(self) -> None:
        lockdown = _StubLockdown()
        routes = _routes_with(lockdown_service=lockdown)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(
            harness, "POST", "/api/disk-guardrails/pause-auto?hours=abc",
        )
        assert response.status == 400


# ---------------------------------------------------------------------------
# POST /api/disk-guardrails/evaluate — handle_evaluate
# ---------------------------------------------------------------------------


class TestEvaluateRoute:
    def test_returns_tick_result(self) -> None:
        captured: dict[str, Any] = {}

        def fake_tick(
            *,
            lockdown_service: Any = None,
            record_history: bool = True,
            min_interval: float | None = None,
        ) -> dict[str, Any]:
            captured.update({
                "lockdown_service": lockdown_service,
                "record_history": record_history,
                "min_interval": min_interval,
            })
            return {
                "ran_at": 1_750_000_000.0,
                "elapsed": 0.012,
                "triggers": [
                    {"rule_id": "storage:lockdown_threshold",
                     "severity": "critical"},
                ],
                "actions": [],
            }

        routes = _routes_with(
            evaluation_loop_tick=fake_tick,
            lockdown_service=_StubLockdown(),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "POST", "/api/disk-guardrails/evaluate")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["elapsed"] == 0.012
        assert len(body["triggers"]) == 1
        # tick called with lockdown service + record_history=False + min_interval=0.
        assert captured["record_history"] is False
        assert captured["min_interval"] == 0


# ---------------------------------------------------------------------------
# Security: CSRF + admin role on every POST
# ---------------------------------------------------------------------------


class _RejectGate:
    def verify(self, handler: Any) -> bool:  # noqa: ARG002
        return False

    def reject(self, handler: Any) -> None:
        from http import HTTPStatus
        handler._json_response(
            HTTPStatus.FORBIDDEN,
            {"error": "CSRF token missing or invalid"},
        )


class TestSecurityGates:
    @pytest.mark.parametrize("path", [
        "/api/disk-guardrails/cleanup",
        "/api/disk-guardrails/lockdown",
        "/api/disk-guardrails/release",
        "/api/disk-guardrails/pause-auto?hours=1",
        "/api/disk-guardrails/evaluate",
    ])
    def test_csrf_rejection_returns_403(self, path: str) -> None:
        routes = DiskGuardrailsRoutes(
            mutation_gate=_RejectGate(),
            admin_gate=_AlwaysAdminGate(),
            audit_appender=_StubAudit(),
            lockdown_service=_StubLockdown(),
            cleanup_runner=_StubCleanup({
                "deleted": 0, "freed_gb": 0.0, "kept": 0,
                "candidates_evaluated": 0, "strategy": "oldest_first",
            }),
            evaluation_loop_tick=lambda **_: {
                "ran_at": 0.0, "elapsed": 0.0,
                "triggers": [], "actions": [],
            },
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "POST", path)
        assert response.status == 403

    @pytest.mark.parametrize("path", [
        "/api/disk-guardrails/cleanup",
        "/api/disk-guardrails/lockdown",
        "/api/disk-guardrails/release",
        "/api/disk-guardrails/pause-auto?hours=1",
        "/api/disk-guardrails/evaluate",
    ])
    def test_admin_role_rejection_returns_403(self, path: str) -> None:
        routes = DiskGuardrailsRoutes(
            mutation_gate=_AlwaysAllowGate(),
            admin_gate=_AlwaysDenyAdminGate(),
            audit_appender=_StubAudit(),
            lockdown_service=_StubLockdown(),
            cleanup_runner=_StubCleanup({
                "deleted": 0, "freed_gb": 0.0, "kept": 0,
                "candidates_evaluated": 0, "strategy": "oldest_first",
            }),
            evaluation_loop_tick=lambda **_: {
                "ran_at": 0.0, "elapsed": 0.0,
                "triggers": [], "actions": [],
            },
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch(harness, "POST", path)
        assert response.status == 403


# ---------------------------------------------------------------------------
# Helper-class unit tests
# ---------------------------------------------------------------------------


class TestHoursQueryParser:
    def test_valid_hours_returns_value(self) -> None:
        parser = HoursQueryParser()
        hours, error = parser.parse("/api/disk-guardrails/pause-auto?hours=3")
        assert error == ""
        assert hours == 3

    def test_zero_rejected(self) -> None:
        parser = HoursQueryParser()
        hours, error = parser.parse("/api/disk-guardrails/pause-auto?hours=0")
        assert error
        assert hours == 0

    def test_clamp_above_max(self) -> None:
        parser = HoursQueryParser()
        hours, error = parser.parse("/api/disk-guardrails/pause-auto?hours=99")
        assert error == ""
        assert hours == 24


class TestActorResolver:
    def test_session_lookup_takes_precedence(self) -> None:
        resolver = ActorResolver(
            session_lookup=lambda h: "matthew",  # noqa: ARG005
            proxy_lookup=lambda h: "envoy",  # noqa: ARG005
        )
        result = resolver.resolve(MockControllerHandler(path="/"))
        assert result == "matthew"

    def test_falls_back_to_proxy(self) -> None:
        resolver = ActorResolver(
            session_lookup=lambda h: "",  # noqa: ARG005
            proxy_lookup=lambda h: "envoy-user",  # noqa: ARG005
        )
        result = resolver.resolve(MockControllerHandler(path="/"))
        assert result == "envoy-user"

    def test_falls_back_to_basic_auth(self) -> None:
        import base64
        resolver = ActorResolver(
            session_lookup=lambda h: "",  # noqa: ARG005
            proxy_lookup=lambda h: "",  # noqa: ARG005
        )
        token = base64.b64encode(b"matthew:secret").decode("ascii")
        handler = MockControllerHandler(
            path="/", headers={"Authorization": f"Basic {token}"},
        )
        assert resolver.resolve(handler) == "matthew"
