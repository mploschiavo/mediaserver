"""Tests for ``api/routes/post_admin_ops.py`` (ADR-0007 Phase 2 wave 5).

This file covers the FIRST POST RouteModule wave. The shape this
test file establishes — gate / collaborator pattern, body+headers
on the mock handler, registry-replacement harness — becomes the
template for subsequent POST waves.

Each test class owns one route. Tests dispatch through the
production Router via ``RouteDispatchHarness.with_default_router()``
so auto-discovery + spec-parity behaviour is exercised end-to-end.

Why so much "shape pinning": the legacy chain's ad-hoc validation
+ persistence shapes were inlined into ``handlers_post``. Lifting
them onto strategy / adapter classes risks accidental shape
regressions; these tests pin the validation, error envelopes, and
service-call wiring at the contract boundary so a change to the
business logic isn't load-bearing on integration tests alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_stack.api.routes.post_admin_ops import (
    AdminOpsPostRoutes,
    AutoHealController,
    BulkGuardrailsService,
    GpuController,
    GuardrailsCadenceService,
    GuardrailsService,
    LogLevelService,
    MediaServerResetService,
    PostMutationGate,
    RestartService,
    RestoreService,
    SnapshotService,
    StackUpgrader,
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
# POST-aware mock handler
# ---------------------------------------------------------------------------


class PostMockHandler(MockControllerHandler):
    """``MockControllerHandler`` extended with a ``_read_json_body``
    that mirrors the production server's behaviour.

    The base ``MockControllerHandler`` only models GET — it has
    ``rfile`` + ``headers`` slots but no body parser. The
    production handler reads ``Content-Length`` from the request
    headers, slices that many bytes off ``self.rfile``, and
    ``json.loads`` the result. We mirror that here so route
    handlers that call ``handler._read_json_body()`` get the
    same shape they'd see in production.
    """

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
        # Auto-set Content-Length so the production reader sees a
        # body. CSRF tests can override by passing it explicitly.
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


@dataclass
class _PostStateStub:
    """Minimal ``state`` stub capturing ``update_config`` writes so
    tests can assert log-level persistence flowed through.

    Mirrors ``ControllerState.update_config`` enough for the
    routes' purposes; the production state object is heavier and
    integration tests already pin the persistence shape.
    """

    persisted: dict[str, Any] = field(default_factory=dict)

    def update_config(self, updates: dict[str, Any]) -> None:
        self.persisted.update(updates)


class _AlwaysAllowGate:
    """Permissive ``PostMutationGate`` stand-in for tests focused
    on business logic.

    Implements the gate's surface (``verify`` / ``reject``) so
    tests can exercise routes without forging CSRF tokens. Routes
    instantiated with this gate skip the 403 branch entirely.
    """

    def verify(self, handler: Any) -> bool:
        return True

    def reject(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError(
            "_AlwaysAllowGate.reject called — verify should have "
            "returned True",
        )


# ---------------------------------------------------------------------------
# Harness — replaces the auto-discovered AdminOpsPostRoutes with a
# test-wired instance so we can inject stubs through the Router
# without re-running discovery.
# ---------------------------------------------------------------------------


class _RouteHarness:
    """Helper that builds a ``RouteDispatchHarness`` with a custom
    ``AdminOpsPostRoutes`` instance.

    Walks the ``Router._exact`` + ``_parameterized`` tables and
    rebinds every method whose ``module_class`` is
    ``AdminOpsPostRoutes`` so the test's collaborators are the ones
    invoked. Mirrors the pattern ``test_ops.py`` uses for its
    OpsGetRoutes replacement.
    """

    @classmethod
    def with_routes(
        cls, routes: AdminOpsPostRoutes,
    ) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Router, routes: AdminOpsPostRoutes) -> None:
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
    def _maybe_replacement(route: Any, routes: AdminOpsPostRoutes) -> Any:
        if "AdminOpsPostRoutes" not in route.display:
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
    """POST helper that uses our POST-aware mock + production
    dispatcher. Matches the harness's ``dispatch`` signature but
    swaps in ``PostMockHandler``."""
    handler = PostMockHandler(
        path=path, body=body, headers=headers, state=state,
    )
    outcome = harness._dispatcher.try_dispatch("POST", path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        harness._dispatcher.write_method_not_allowed(handler, path)
    return handler.captured


def _routes_with(**kwargs: Any) -> AdminOpsPostRoutes:
    """Build an ``AdminOpsPostRoutes`` with the always-allow gate
    pre-wired so tests don't re-state it.
    """
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    return AdminOpsPostRoutes(**kwargs)


# ---------------------------------------------------------------------------
# /api/stack/upgrade — StackUpgrader
# ---------------------------------------------------------------------------


class TestStackUpgradeRoute:
    def test_forwards_target_field_to_upgrader(self) -> None:
        captured: dict[str, Any] = {}

        def fake_start(target: Any) -> dict[str, Any]:
            captured["target"] = target
            return {"accepted": True, "task_id": "stack-upgrade-1"}

        routes = _routes_with(stack_upgrader=StackUpgrader(start_fn=fake_start))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/stack/upgrade", body={"target": "1.0.207"},
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "accepted": True, "task_id": "stack-upgrade-1",
        }
        assert captured["target"] == "1.0.207"

    def test_missing_target_passes_none(self) -> None:
        captured: dict[str, Any] = {}

        def fake_start(target: Any) -> dict[str, Any]:
            captured["target"] = target
            return {"accepted": False, "error": "no target"}

        routes = _routes_with(stack_upgrader=StackUpgrader(start_fn=fake_start))
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(harness, "/api/stack/upgrade", body=b"")

        assert captured["target"] is None


# ---------------------------------------------------------------------------
# /api/restart/{service} — RestartService
# ---------------------------------------------------------------------------


class TestRestartServiceRoute:
    def test_known_service_restarts(self) -> None:
        restart_calls: list[str] = []

        rs = RestartService(
            restart_fn=lambda svc: (
                restart_calls.append(svc) or {"status": "restarted", "method": "k8s"}
            ),
            service_map_provider=lambda: {"sonarr": object(), "radarr": object()},
        )
        routes = _routes_with(restart_service=rs)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/restart/sonarr")

        assert response.status == 200
        assert json.loads(response.body) == {
            "status": "restarted", "method": "k8s",
        }
        assert restart_calls == ["sonarr"]

    def test_controller_special_case_accepted(self) -> None:
        """``controller`` isn't in the SERVICE_MAP but the route
        explicitly allows it — same special case the legacy chain
        carved out for restarting the controller pod itself.
        """
        rs = RestartService(
            restart_fn=lambda svc: {"status": "restarted", "method": "k8s"},
            service_map_provider=lambda: {"sonarr": object()},
        )
        routes = _routes_with(restart_service=rs)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/restart/controller")

        assert response.status == 200

    def test_unknown_service_returns_400_with_known_set(self) -> None:
        rs = RestartService(
            restart_fn=lambda svc: pytest.fail("should not call restart"),
            service_map_provider=lambda: {"sonarr": object(), "radarr": object()},
        )
        routes = _routes_with(restart_service=rs)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/restart/typo-svc")

        assert response.status == 400
        body = json.loads(response.body)
        assert body["error"] == "Unknown service 'typo-svc'"
        assert sorted(body["known"]) == ["radarr", "sonarr"]


# ---------------------------------------------------------------------------
# /api/batch-restart — RestartService.restart_many
# ---------------------------------------------------------------------------


class TestBatchRestartRoute:
    def test_forwards_services_list(self) -> None:
        captured: dict[str, Any] = {}

        def _batch(svcs: list[str]) -> dict[str, Any]:
            captured["services"] = svcs
            return {"results": {s: "restarted" for s in svcs}}

        rs = RestartService(batch_fn=_batch)
        routes = _routes_with(restart_service=rs)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/batch-restart",
            body={"services": ["sonarr", "radarr"]},
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "results": {"sonarr": "restarted", "radarr": "restarted"},
        }
        assert captured["services"] == ["sonarr", "radarr"]

    def test_empty_list_returns_400(self) -> None:
        rs = RestartService(
            batch_fn=lambda svcs: pytest.fail("should not call batch"),
        )
        routes = _routes_with(restart_service=rs)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/batch-restart", body={"services": []},
        )

        assert response.status == 400
        assert json.loads(response.body) == {
            "error": "services list required",
        }

    def test_missing_services_field_returns_400(self) -> None:
        rs = RestartService(batch_fn=lambda svcs: pytest.fail("nope"))
        routes = _routes_with(restart_service=rs)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/batch-restart", body={},
        )

        assert response.status == 400


# ---------------------------------------------------------------------------
# /api/restore — RestoreService
# ---------------------------------------------------------------------------


class TestRestoreRoute:
    def test_calls_restore_with_body_and_state(self) -> None:
        captured: dict[str, Any] = {}

        def fake_restore(body: dict[str, Any], state: Any) -> dict[str, Any]:
            captured["body"] = body
            captured["state"] = state
            return {"status": "ok", "restored": ["sonarr/config.xml"]}

        state = _PostStateStub()
        routes = _routes_with(restore_service=RestoreService(restore_fn=fake_restore))
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/restore",
            body={"service_configs": {"sonarr/config.xml": "<x/>"}},
            state=state,
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "status": "ok", "restored": ["sonarr/config.xml"],
        }
        assert captured["state"] is state
        assert "sonarr/config.xml" in captured["body"]["service_configs"]

    def test_missing_service_configs_returns_400(self) -> None:
        routes = _routes_with(
            restore_service=RestoreService(
                restore_fn=lambda b, s: pytest.fail("should not call"),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/restore", body={"foo": "bar"})

        assert response.status == 400
        assert "service_configs" in json.loads(response.body)["error"]

    def test_empty_body_returns_400(self) -> None:
        routes = _routes_with(
            restore_service=RestoreService(restore_fn=lambda b, s: {}),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/restore", body=b"")

        assert response.status == 400


# ---------------------------------------------------------------------------
# /api/snapshot — SnapshotService
# ---------------------------------------------------------------------------


class TestSnapshotRoute:
    def test_returns_take_snapshot_result(self) -> None:
        snapshot_payload = {
            "status": "created",
            "file": "snapshot-20260503T120000.json",
            "configs": 11,
        }
        routes = _routes_with(
            snapshot_service=SnapshotService(take_fn=lambda: snapshot_payload),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/snapshot")

        assert response.status == 200
        assert json.loads(response.body) == snapshot_payload


# ---------------------------------------------------------------------------
# /api/log-level — LogLevelService
# ---------------------------------------------------------------------------


class TestLogLevelRoute:
    def _routes(
        self,
        *,
        set_level_fn: Any = None,
        log_fn: Any = None,
    ) -> AdminOpsPostRoutes:
        return _routes_with(
            log_level_service=LogLevelService(
                set_level_fn=set_level_fn,
                log_fn=log_fn,
            ),
        )

    def test_valid_level_persists_and_returns_new_value(self) -> None:
        log_messages: list[str] = []
        routes = self._routes(
            set_level_fn=lambda level: level,
            log_fn=log_messages.append,
        )
        harness = _RouteHarness.with_routes(routes)
        state = _PostStateStub()
        response = _dispatch_post(
            harness, "/api/log-level",
            body={"level": "debug"}, state=state,
        )

        assert response.status == 200
        assert json.loads(response.body) == {"level": "DEBUG"}
        assert state.persisted == {"_log_level": "DEBUG"}
        assert log_messages == ["[INFO] Log level changed to DEBUG"]

    def test_invalid_level_returns_400_with_allowed_set(self) -> None:
        routes = self._routes(set_level_fn=lambda l: l)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/log-level",
            body={"level": "TRACE"}, state=_PostStateStub(),
        )

        assert response.status == 400
        body = json.loads(response.body)
        assert "Invalid log level" in body["error"]
        assert body["valid"] == ["DEBUG", "INFO", "WARN", "ERROR"]

    def test_missing_level_returns_400(self) -> None:
        routes = self._routes(set_level_fn=lambda l: l)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/log-level", body={}, state=_PostStateStub(),
        )

        assert response.status == 400


# ---------------------------------------------------------------------------
# /api/gpu/enable — GpuController
# ---------------------------------------------------------------------------


class TestGpuEnableRoute:
    def test_returns_gpu_result(self) -> None:
        gpu_payload = {"status": "ok", "hw_accel_type": "vaapi"}
        routes = _routes_with(
            gpu_controller=GpuController(enable_fn=lambda: gpu_payload),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/gpu/enable")

        assert response.status == 200
        assert json.loads(response.body) == gpu_payload


# ---------------------------------------------------------------------------
# /api/auto-heal/run + /api/auto-heal/enabled — AutoHealController
# ---------------------------------------------------------------------------


class TestAutoHealRoutes:
    def test_run_returns_cycle_result(self) -> None:
        ah = AutoHealController(run_fn=lambda: {"healed": ["sonarr"], "ok": True})
        routes = _routes_with(auto_heal_controller=ah)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/auto-heal/run")

        assert response.status == 200
        assert json.loads(response.body) == {"healed": ["sonarr"], "ok": True}

    def test_enabled_toggles_to_explicit_value(self) -> None:
        captured: dict[str, Any] = {}
        ah = AutoHealController(
            set_enabled_fn=lambda v: (
                captured.setdefault("value", v) or {"enabled": v}
            ),
        )
        routes = _routes_with(auto_heal_controller=ah)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/auto-heal/enabled", body={"enabled": False},
        )

        assert response.status == 200
        assert json.loads(response.body) == {"enabled": False}
        assert captured["value"] is False

    def test_enabled_defaults_to_true_when_omitted(self) -> None:
        captured: dict[str, Any] = {}
        ah = AutoHealController(
            set_enabled_fn=lambda v: (
                captured.setdefault("value", v) or {"enabled": v}
            ),
        )
        routes = _routes_with(auto_heal_controller=ah)
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(harness, "/api/auto-heal/enabled", body={})

        assert captured["value"] is True


# ---------------------------------------------------------------------------
# /api/guardrails/config — GuardrailsCadenceService
# ---------------------------------------------------------------------------


class TestGuardrailsConfigRoute:
    def _routes(self, env_setter: Any = None) -> AdminOpsPostRoutes:
        return _routes_with(
            guardrails_cadence=GuardrailsCadenceService(env_setter=env_setter),
        )

    def test_valid_interval_persists_to_env(self) -> None:
        env_writes: dict[str, str] = {}
        routes = self._routes(env_setter=env_writes.__setitem__)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails/config",
            body={"evaluation_interval_seconds": 600},
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "evaluation_interval_seconds": 600,
        }
        assert env_writes == {
            "MEDIA_STACK_GUARDRAIL_INTERVAL_SECONDS": "600",
        }

    def test_floor_violation_returns_400(self) -> None:
        env_writes: dict[str, str] = {}
        routes = self._routes(env_setter=env_writes.__setitem__)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails/config",
            body={"evaluation_interval_seconds": 5},
        )

        assert response.status == 400
        assert env_writes == {}

    def test_ceiling_violation_returns_400(self) -> None:
        env_writes: dict[str, str] = {}
        routes = self._routes(env_setter=env_writes.__setitem__)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails/config",
            body={"evaluation_interval_seconds": 999999},
        )

        assert response.status == 400
        assert env_writes == {}

    def test_non_integer_returns_400(self) -> None:
        env_writes: dict[str, str] = {}
        routes = self._routes(env_setter=env_writes.__setitem__)
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails/config",
            body={"evaluation_interval_seconds": "not-a-number"},
        )

        assert response.status == 400
        assert env_writes == {}


# ---------------------------------------------------------------------------
# /api/guardrails — BulkGuardrailsService
# ---------------------------------------------------------------------------


class TestGuardrailsBulkUpdateRoute:
    def test_forwards_body_to_disk_svc(self) -> None:
        captured: dict[str, Any] = {}

        def _update(body: dict[str, Any]) -> dict[str, Any]:
            captured["body"] = body
            return {"status": "ok", "changed": list(body.keys())}

        routes = _routes_with(
            bulk_guardrails=BulkGuardrailsService(update_fn=_update),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails",
            body={"max_used_percent": 80, "qbit_min_ratio": 1.5},
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "ok"
        assert sorted(body["changed"]) == ["max_used_percent", "qbit_min_ratio"]
        assert captured["body"] == {"max_used_percent": 80, "qbit_min_ratio": 1.5}

    def test_empty_body_returns_400(self) -> None:
        routes = _routes_with(
            bulk_guardrails=BulkGuardrailsService(
                update_fn=lambda b: pytest.fail("should not call"),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/guardrails", body=b"")

        assert response.status == 400


# ---------------------------------------------------------------------------
# /api/guardrails/{id} — GuardrailsService.update_threshold
# /api/guardrails/{id}/test — GuardrailsService.test
# /api/guardrails/{id}/disable — GuardrailsService.set_disabled
# ---------------------------------------------------------------------------


@dataclass
class _FakeTrigger:
    severity: str = "warn"
    current_value: dict[str, Any] = field(default_factory=lambda: {"pct": 92.0})
    threshold: dict[str, Any] = field(default_factory=lambda: {"pct": 90})
    description: str = "Per-mount disk usage above threshold"


class _FakeRegistry:
    """Stand-in for the real ``GuardrailRegistry`` covering the
    five methods the route module calls.

    Constructor accepts a ``known_ids`` set + a ``trigger_for``
    map so tests can dial in what the dry-run returns. Each call
    is recorded so tests can assert pass-through behaviour.
    """

    def __init__(
        self,
        *,
        known_ids: set[str] | None = None,
        triggers: dict[str, _FakeTrigger | None] | None = None,
        thresholds: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._known = set(known_ids or ())
        self._triggers = triggers or {}
        self._thresholds = thresholds or {}
        self.calls: list[tuple[str, Any]] = []

    def get(self, rule_id: str) -> Any:
        return object() if rule_id in self._known else None

    def threshold_for(self, rule_id: str) -> dict[str, Any]:
        return self._thresholds.get(rule_id, {"pct": 90})

    def update_threshold(
        self, rule_id: str, threshold: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(("update_threshold", (rule_id, threshold)))
        return {"ok": True, "id": rule_id, "threshold": threshold}

    def set_disabled(self, rule_id: str, disabled: bool) -> dict[str, Any]:
        self.calls.append(("set_disabled", (rule_id, disabled)))
        return {"ok": True, "id": rule_id, "disabled": disabled}

    def evaluate_one(
        self, rule_id: str, snapshot: dict[str, Any],
    ) -> _FakeTrigger | None:
        self.calls.append(("evaluate_one", (rule_id, snapshot)))
        return self._triggers.get(rule_id)


class TestGuardrailsThresholdRoute:
    def test_known_rule_updates_threshold(self) -> None:
        registry = _FakeRegistry(known_ids={"storage:per_mount_threshold"})
        routes = _routes_with(
            guardrails_service=GuardrailsService(
                registry_provider=lambda: registry,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails/storage:per_mount_threshold",
            body={"threshold": {"pct": 95}},
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "ok": True,
            "id": "storage:per_mount_threshold",
            "threshold": {"pct": 95},
        }
        assert registry.calls == [
            (
                "update_threshold",
                ("storage:per_mount_threshold", {"pct": 95}),
            ),
        ]

    def test_unknown_rule_returns_404(self) -> None:
        registry = _FakeRegistry(known_ids=set())
        routes = _routes_with(
            guardrails_service=GuardrailsService(
                registry_provider=lambda: registry,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails/bogus", body={"threshold": {}},
        )

        assert response.status == 404
        assert "unknown guardrail" in json.loads(response.body)["error"]

    def test_missing_threshold_object_returns_400(self) -> None:
        registry = _FakeRegistry(known_ids={"foo"})
        routes = _routes_with(
            guardrails_service=GuardrailsService(
                registry_provider=lambda: registry,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails/foo", body={"threshold": "not-a-dict"},
        )

        assert response.status == 400


class TestGuardrailsTestRoute:
    def test_firing_rule_returns_full_envelope(self) -> None:
        trigger = _FakeTrigger()
        registry = _FakeRegistry(
            known_ids={"foo"}, triggers={"foo": trigger},
        )
        routes = _routes_with(
            guardrails_service=GuardrailsService(
                registry_provider=lambda: registry,
                state_collector_fn=lambda: {"foo_metric": 99.5},
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/guardrails/foo/test")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "would_trigger": True,
            "severity": "warn",
            "current_value": {"pct": 92.0},
            "threshold": {"pct": 90},
            "description": "Per-mount disk usage above threshold",
        }

    def test_quiet_rule_returns_would_trigger_false(self) -> None:
        registry = _FakeRegistry(known_ids={"foo"}, triggers={"foo": None})
        routes = _routes_with(
            guardrails_service=GuardrailsService(
                registry_provider=lambda: registry,
                state_collector_fn=lambda: {},
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/guardrails/foo/test")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "would_trigger": False,
            "severity": None,
            "current_value": None,
            "threshold": {"pct": 90},
        }

    def test_test_threads_threshold_into_state(self) -> None:
        """The legacy chain seeds the state snapshot with
        ``_threshold:<rule_id>`` so the rule's evaluator can see
        the override. Pin that wiring."""
        registry = _FakeRegistry(known_ids={"foo"}, triggers={"foo": None})
        routes = _routes_with(
            guardrails_service=GuardrailsService(
                registry_provider=lambda: registry,
                state_collector_fn=lambda: {"base": "snapshot"},
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(harness, "/api/guardrails/foo/test")

        evaluate_call = next(c for c in registry.calls if c[0] == "evaluate_one")
        snapshot = evaluate_call[1][1]
        assert snapshot["base"] == "snapshot"
        assert "_threshold:foo" in snapshot

    def test_unknown_rule_returns_404(self) -> None:
        registry = _FakeRegistry(known_ids=set())
        routes = _routes_with(
            guardrails_service=GuardrailsService(
                registry_provider=lambda: registry,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/guardrails/missing/test")

        assert response.status == 404


class TestGuardrailsDisableRoute:
    def test_disable_defaults_to_true(self) -> None:
        registry = _FakeRegistry(known_ids={"foo"})
        routes = _routes_with(
            guardrails_service=GuardrailsService(
                registry_provider=lambda: registry,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails/foo/disable", body={},
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "ok": True, "id": "foo", "disabled": True,
        }
        assert registry.calls == [("set_disabled", ("foo", True))]

    def test_disable_false_re_enables(self) -> None:
        registry = _FakeRegistry(known_ids={"foo"})
        routes = _routes_with(
            guardrails_service=GuardrailsService(
                registry_provider=lambda: registry,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails/foo/disable",
            body={"disabled": False},
        )

        assert response.status == 200
        assert registry.calls == [("set_disabled", ("foo", False))]

    def test_unknown_rule_returns_404(self) -> None:
        registry = _FakeRegistry(known_ids=set())
        routes = _routes_with(
            guardrails_service=GuardrailsService(
                registry_provider=lambda: registry,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/guardrails/missing/disable", body={"disabled": True},
        )

        assert response.status == 404


# ---------------------------------------------------------------------------
# /api/media-server/reset — MediaServerResetService
# ---------------------------------------------------------------------------


class TestMediaServerResetRoute:
    def test_uses_body_credentials(self) -> None:
        captured: dict[str, Any] = {}

        def fake_reset(username: str, password: str) -> dict[str, Any]:
            captured["creds"] = (username, password)
            return {"ok": True, "service": "jellyfin", "reset_via": "db"}

        routes = _routes_with(
            media_server_reset=MediaServerResetService(
                reset_fn=fake_reset,
                env_provider=lambda *_: "",
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/media-server/reset",
            body={"username": "alice", "password": "supersecret"},
        )

        assert response.status == 200
        assert captured["creds"] == ("alice", "supersecret")

    def test_falls_back_to_env_when_body_omits_fields(self) -> None:
        captured: dict[str, Any] = {}

        def fake_reset(username: str, password: str) -> dict[str, Any]:
            captured["creds"] = (username, password)
            return {"ok": True}

        env = {
            "STACK_ADMIN_USERNAME": "envuser",
            "STACK_ADMIN_PASSWORD": "envpass1234",
        }
        routes = _routes_with(
            media_server_reset=MediaServerResetService(
                reset_fn=fake_reset,
                env_provider=lambda key, default="": env.get(key, default),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/media-server/reset", body={},
        )

        assert response.status == 200
        assert captured["creds"] == ("envuser", "envpass1234")

    def test_short_password_returns_400(self) -> None:
        routes = _routes_with(
            media_server_reset=MediaServerResetService(
                reset_fn=lambda u, p: pytest.fail("should not call"),
                env_provider=lambda *_: "",
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/media-server/reset",
            body={"password": "abc"},
        )

        assert response.status == 400
        assert "min" in json.loads(response.body)["error"]


# ---------------------------------------------------------------------------
# CSRF gate — security regression coverage
# ---------------------------------------------------------------------------


class TestPostMutationGate:
    """Pin the CSRF double-submit semantics for migrated POST
    routes. The legacy chain enforced this in
    ``_global_preflight``; the Router-dispatched path bypasses
    that, so this gate is the only line of defence — regressions
    here would re-open the CSRF surface for every migrated route.
    """

    def test_no_cookie_no_enforce_passes(self, monkeypatch) -> None:
        monkeypatch.delenv("CSRF_ENFORCE", raising=False)
        gate = PostMutationGate()
        handler = PostMockHandler(headers={})
        assert gate.verify(handler) is True

    def test_cookie_without_csrf_header_rejected(self, monkeypatch) -> None:
        """Browser request: ``Cookie`` set, ``X-CSRF-Token`` missing
        → reject. Same shape ``handlers_post._check_csrf`` enforces.
        """
        monkeypatch.delenv("CSRF_ENFORCE", raising=False)
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        handler = PostMockHandler(headers={"Cookie": "media_stack_csrf=abc"})

        assert gate.verify(handler) is False
        csrf_stub.verify.assert_called_once_with(
            cookie_header="media_stack_csrf=abc",
            header_value="",
        )

    def test_strict_mode_rejects_even_without_cookie(self, monkeypatch) -> None:
        monkeypatch.setenv("CSRF_ENFORCE", "1")
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        handler = PostMockHandler(headers={})

        assert gate.verify(handler) is False

    def test_disabled_mode_short_circuits(self, monkeypatch) -> None:
        monkeypatch.setenv("CSRF_ENFORCE", "0")
        csrf_stub = MagicMock()
        gate = PostMutationGate(csrf=csrf_stub)
        handler = PostMockHandler(headers={"Cookie": "x=y"})

        assert gate.verify(handler) is True
        csrf_stub.verify.assert_not_called()

    def test_reject_writes_403(self) -> None:
        gate = PostMutationGate()
        handler = PostMockHandler()
        gate.reject(handler)
        assert handler.captured.status == 403
        assert "CSRF" in json.loads(handler.captured.body)["error"]

    def test_route_blocks_when_gate_rejects(self) -> None:
        """End-to-end: a route with the real gate + a CSRF stub
        that returns False must emit 403 instead of running the
        adapter. Pins that the gate fires before any business
        logic.
        """
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        called: list[Any] = []

        def boom() -> dict[str, Any]:
            called.append(True)
            return {}

        routes = AdminOpsPostRoutes(
            mutation_gate=gate,
            snapshot_service=SnapshotService(take_fn=boom),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/snapshot",
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert called == []


# ---------------------------------------------------------------------------
# Routing integration — auto-discovery + spec-parity
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity for the admin-ops POST
    domain. If a future change accidentally drops a handler, this
    fires before any per-route test does.
    """

    _EXPECTED = frozenset({
        "/api/stack/upgrade",
        "/api/restart/{service}",
        "/api/batch-restart",
        "/api/restore",
        "/api/snapshot",
        "/api/log-level",
        "/api/gpu/enable",
        "/api/auto-heal/run",
        "/api/auto-heal/enabled",
        "/api/guardrails/config",
        "/api/guardrails",
        "/api/guardrails/{id}",
        "/api/guardrails/{id}/test",
        "/api/guardrails/{id}/disable",
        "/api/media-server/reset",
        "/api/lifecycle-ensurers/{service}/{method}",
    })

    def test_all_admin_ops_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST"
            and "AdminOpsPostRoutes" in r.display
        }
        assert registered == self._EXPECTED, (
            f"Mismatch — missing: {self._EXPECTED - registered}, "
            f"unexpected: {registered - self._EXPECTED}"
        )

    def test_default_constructor_wires_real_collaborators(self) -> None:
        """Auto-discovery instantiates ``AdminOpsPostRoutes()`` with
        no kwargs — the default collaborators must construct
        cleanly even though they lazy-import their service modules.
        Pins that no default ``__init__`` raises.
        """
        instance = AdminOpsPostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(instance._stack_upgrader, StackUpgrader)
        assert isinstance(instance._restart, RestartService)
        assert isinstance(instance._snapshot, SnapshotService)
        assert isinstance(instance._log_level, LogLevelService)
        assert isinstance(instance._gpu, GpuController)
        assert isinstance(instance._auto_heal, AutoHealController)
        assert isinstance(instance._cadence, GuardrailsCadenceService)
        assert isinstance(instance._bulk_guardrails, BulkGuardrailsService)
        assert isinstance(instance._guardrails, GuardrailsService)
        assert isinstance(instance._restore, RestoreService)
        assert isinstance(instance._media_server_reset, MediaServerResetService)

    def test_get_to_post_only_path_returns_method_not_allowed(self) -> None:
        """``/api/stack/upgrade`` is POST-only in the spec; a GET
        should land on METHOD_NOT_ALLOWED, not on the legacy
        chain. Pins the dispatcher's verb-discrimination.
        """
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("GET", "/api/stack/upgrade")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
