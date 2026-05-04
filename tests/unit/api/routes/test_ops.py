"""Tests for ``api/routes/ops.py`` (ADR-0007 Phase 2 wave 4).

Each test class owns one route. Tests invoke the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

Why so much "shape pinning": the run-history payload, the
orchestrator promise-state payload, and the telemetry metrics
body are read by:

* the dashboard's Jobs page (``/api/runs`` row table —
  ``parent_job_name`` field, ``runs[]`` envelope shape),
* ``FreshInstallVerifier`` (ADR-0004 — both 200 + 503 modes of
  ``/api/orchestrator/promises/state``),
* the dashboard's Telemetry diagnostics card.

Integration tests catch shape regressions late and noisily; these
unit tests catch them at the contract boundary, so they assert
exact JSON shapes rather than just status codes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from media_stack.api.routes.ops import (
    OpsGetRoutes,
    OrchestratorStateAdapter,
    RunHistoryRepository,
    TelemetrySource,
)
from tests.unit.api.routes._helpers import (
    MockControllerHandler,
    RouteDispatchHarness,
)


def _dispatch_with_query(
    harness: RouteDispatchHarness,
    verb: str,
    raw_path: str,
) -> Any:
    """Mirror the production server's path/query split before
    handing off to the dispatcher.

    ``server.py::do_GET`` does
    ``path = self.path.split("?", 1)[0]`` and passes the BARE
    path to the dispatcher while leaving ``self.path`` intact
    with the query string attached. The route handler reads
    ``handler.path`` for query-string-aware logic. Tests that
    exercise that flow need to do the same split — the harness
    calls ``try_dispatch(verb, path, handler)`` so it can't do
    the split for us when the caller wants the FULL path on the
    handler.
    """
    bare_path = raw_path.split("?", 1)[0]
    handler = MockControllerHandler(path=raw_path)
    harness._dispatcher.try_dispatch(verb, bare_path, handler)
    return handler.captured


# ---------------------------------------------------------------------------
# Fakes for run_history / orchestrator_state / telemetry
# ---------------------------------------------------------------------------


@dataclass
class _FakeRunRecord:
    """Minimal stand-in matching the ``RunRecord.to_dict``
    contract. Tests use this rather than the real dataclass to
    keep the assertion shape literal — when a future change
    accidentally drops a field from the wire shape, this test
    fails on the exact key, not on a far-removed dataclass diff.
    """

    run_id: str
    job_name: str
    status: str = "ok"
    started_at: float = 1777140000.0
    parent_run_id: str | None = None
    batch_id: str | None = None
    elapsed: float | None = 1.5

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": self.run_id,
            "job_name": self.job_name,
            "status": self.status,
            "started_at": self.started_at,
            "elapsed": self.elapsed,
        }
        if self.parent_run_id is not None:
            out["parent_run_id"] = self.parent_run_id
        if self.batch_id is not None:
            out["batch_id"] = self.batch_id
        return out


@dataclass
class _FakeRunHistoryModule:
    """Stand-in for the real ``run_history`` module with the
    three functions ``RunHistoryRepository`` calls.

    ``records`` doubles as the buffer for ``iter_records`` AND the
    pool for ``get_runs`` filtering. Tests configure both via the
    same list — keeps the parent-name lookup correct under filter
    pressure (a child whose parent is filtered out of the
    returned slice still resolves its parent name from the full
    buffer).
    """

    records: list[_FakeRunRecord] = field(default_factory=list)
    last_get_runs_kwargs: dict[str, Any] = field(default_factory=dict)

    def get_runs(self, **kwargs: Any) -> list[_FakeRunRecord]:
        # Capture the kwargs so tests can assert query-string
        # parsing flowed through to the persistence layer correctly.
        self.last_get_runs_kwargs = dict(kwargs)
        out: list[_FakeRunRecord] = []
        for r in self.records:
            if (
                kwargs.get("job_name") is not None
                and r.job_name != kwargs["job_name"]
            ):
                continue
            if (
                kwargs.get("parent_run_id") is not None
                and r.parent_run_id != kwargs["parent_run_id"]
            ):
                continue
            if (
                kwargs.get("batch_id") is not None
                and r.batch_id != kwargs["batch_id"]
            ):
                continue
            if (
                kwargs.get("since_ts") is not None
                and r.started_at < kwargs["since_ts"]
            ):
                continue
            out.append(r)
        limit = int(kwargs.get("limit", 100))
        return out[:limit]

    def iter_records(self) -> list[_FakeRunRecord]:
        return list(self.records)


# ---------------------------------------------------------------------------
# /api/runs — RunHistoryRepository + route shape
# ---------------------------------------------------------------------------


class TestRunsRoute:
    """``GET /api/runs`` — run-history listing.

    The integration tests pin the response envelope as
    ``{"runs": [...]}``. The dashboard reads ``parent_job_name``
    when present; orphans (parent not in buffer) get NO field
    (not an empty string). Tests pin both.
    """

    def _harness_with_repo(
        self, repo: RunHistoryRepository,
    ) -> RouteDispatchHarness:
        from media_stack.api.routing import (
            DefaultDispatcher,
            Router,
            RouterDispatcher,
        )

        DefaultDispatcher.reset_for_tests()
        router = Router()
        # The Router auto-discovered the default OpsGetRoutes
        # already; replace the bound handler for the /api/runs route
        # with one wired to our test repo so we don't touch the real
        # JSONL buffer.
        replacement = OpsGetRoutes(run_history_repository=repo)
        # Rewrite both exact + parameterized maps in place.
        for key, route in list(router._exact.items()):
            if route.path == "/api/runs":
                router._exact[key] = type(route)(
                    verb=route.verb,
                    path=route.path,
                    handler=replacement.handle_runs,
                    pattern=route.pattern,
                    param_names=route.param_names,
                    display=route.display,
                )
        return RouteDispatchHarness(RouterDispatcher(router))

    def test_returns_runs_envelope_shape(self) -> None:
        fake = _FakeRunHistoryModule(records=[
            _FakeRunRecord(
                run_id="r1",
                job_name="ensure-jellyfin-libraries",
                status="ok",
                started_at=1777140000.0,
            ),
            _FakeRunRecord(
                run_id="r2",
                job_name="reconcile-quality-profiles",
                status="failed_transient",
                started_at=1777140100.0,
                parent_run_id="r1",
            ),
        ])
        repo = RunHistoryRepository(run_history_module=fake)
        harness = self._harness_with_repo(repo)

        response = harness.dispatch("GET", "/api/runs")

        assert response.status == 200
        body = json.loads(response.body)
        assert set(body.keys()) == {"runs"}
        assert len(body["runs"]) == 2
        # First row: no parent => NO parent_job_name field.
        assert body["runs"][0] == {
            "run_id": "r1",
            "job_name": "ensure-jellyfin-libraries",
            "status": "ok",
            "started_at": 1777140000.0,
            "elapsed": 1.5,
        }
        # Second row: parent r1 in buffer => parent_job_name inlined.
        assert body["runs"][1] == {
            "run_id": "r2",
            "job_name": "reconcile-quality-profiles",
            "status": "failed_transient",
            "started_at": 1777140100.0,
            "elapsed": 1.5,
            "parent_run_id": "r1",
            "parent_job_name": "ensure-jellyfin-libraries",
        }

    def test_orphan_child_has_no_parent_job_name_field(self) -> None:
        """Parent rotated out of the persistence window → NO
        ``parent_job_name`` field. Confirming "no field" not
        "empty string" so the dashboard's
        ``row.parent_job_name && ...`` check stays correct.
        """
        fake = _FakeRunHistoryModule(records=[
            _FakeRunRecord(
                run_id="orphan",
                job_name="late-job",
                parent_run_id="rotated-out",
            ),
        ])
        repo = RunHistoryRepository(run_history_module=fake)
        harness = self._harness_with_repo(repo)

        response = harness.dispatch("GET", "/api/runs")

        body = json.loads(response.body)
        assert "parent_job_name" not in body["runs"][0]
        assert body["runs"][0]["parent_run_id"] == "rotated-out"

    def test_query_string_filters_flow_to_repository(self) -> None:
        """``/api/runs?job=foo&since=100&limit=5&parent=p1&batch=b1``
        — every filter must flow through to ``get_runs`` with the
        right kwarg name + type. Pins the parsing contract.
        """
        fake = _FakeRunHistoryModule()
        repo = RunHistoryRepository(run_history_module=fake)
        harness = self._harness_with_repo(repo)

        _dispatch_with_query(
            harness,
            "GET",
            "/api/runs?job=foo&since=100&limit=5&parent=p1&batch=b1",
        )

        assert fake.last_get_runs_kwargs == {
            "job_name": "foo",
            "since_ts": 100.0,
            "parent_run_id": "p1",
            "batch_id": "b1",
            "limit": 5,
        }

    def test_limit_clamped_to_floor_and_ceiling(self) -> None:
        """``limit=0`` raises to floor (1). ``limit=99999999`` drops
        to ceiling (50000). Same clamp the legacy chain enforced.
        """
        fake = _FakeRunHistoryModule()
        repo = RunHistoryRepository(run_history_module=fake)
        harness = self._harness_with_repo(repo)

        _dispatch_with_query(harness, "GET", "/api/runs?limit=0")
        assert fake.last_get_runs_kwargs["limit"] == 1

        _dispatch_with_query(harness, "GET", "/api/runs?limit=99999999")
        assert fake.last_get_runs_kwargs["limit"] == 50000

    def test_invalid_limit_falls_back_to_default(self) -> None:
        """Non-integer ``limit`` → 100 (default). Same fallback the
        legacy chain used.
        """
        fake = _FakeRunHistoryModule()
        repo = RunHistoryRepository(run_history_module=fake)
        harness = self._harness_with_repo(repo)

        _dispatch_with_query(harness, "GET", "/api/runs?limit=not-a-number")
        assert fake.last_get_runs_kwargs["limit"] == 100

    def test_invalid_since_falls_back_to_none(self) -> None:
        fake = _FakeRunHistoryModule()
        repo = RunHistoryRepository(run_history_module=fake)
        harness = self._harness_with_repo(repo)

        _dispatch_with_query(harness, "GET", "/api/runs?since=garbage")
        assert fake.last_get_runs_kwargs["since_ts"] is None

    def test_no_query_string_uses_defaults(self) -> None:
        fake = _FakeRunHistoryModule()
        repo = RunHistoryRepository(run_history_module=fake)
        harness = self._harness_with_repo(repo)

        harness.dispatch("GET", "/api/runs")

        assert fake.last_get_runs_kwargs == {
            "job_name": None,
            "since_ts": None,
            "parent_run_id": None,
            "batch_id": None,
            "limit": 100,
        }


# ---------------------------------------------------------------------------
# /api/orchestrator/promises/state — OrchestratorStateAdapter
# ---------------------------------------------------------------------------


class TestOrchestratorPromisesStateRoute:
    """``GET /api/orchestrator/promises/state``.

    Two non-200 modes both carry ``last_tick_age_seconds`` so the
    verifier can tell "no state yet" from "state is stale". Tests
    pin both modes' shapes.
    """

    def _200_payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "saved_at": 1777140000.0,
            "last_tick_age_seconds": 12.3,
            "platform": "k8s",
            "live_services": ["sonarr", "radarr"],
            "totals": {
                "total": 42,
                "ok": 40,
                "failed_transient": 1,
                "failed_permanent": 0,
                "skipped_cooldown": 1,
                "skipped_platform": 0,
                "unknown": 0,
            },
            "attempts": [],
        }

    def test_returns_persisted_state_on_200(self) -> None:
        body = self._200_payload()
        with patch(
            "media_stack.api.routes.ops.OrchestratorStateAdapter.fetch",
            return_value=(200, body),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/orchestrator/promises/state",
            )

        assert response.status == 200
        assert json.loads(response.body) == body

    def test_returns_503_when_state_missing_or_stale(self) -> None:
        """503 must still carry ``last_tick_age_seconds`` (or
        ``None``) so the verifier can choose retry vs. give-up
        without re-parsing.
        """
        stale_body = {
            "error": "orchestrator state not yet persisted",
            "saved_at": None,
            "last_tick_age_seconds": None,
        }
        with patch(
            "media_stack.api.routes.ops.OrchestratorStateAdapter.fetch",
            return_value=(503, stale_body),
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "GET", "/api/orchestrator/promises/state",
            )

        assert response.status == 503
        body = json.loads(response.body)
        assert "last_tick_age_seconds" in body
        assert body["saved_at"] is None

    def test_adapter_delegates_to_injected_read_fn(self) -> None:
        """The adapter is a thin pass-through; tests can swap the
        ``read_fn`` constructor arg to avoid touching the on-disk
        state file. Pin that contract.
        """
        sentinel = (200, {"saved_at": 999.0})
        adapter = OrchestratorStateAdapter(read_fn=lambda: sentinel)
        assert adapter.fetch() == sentinel


# ---------------------------------------------------------------------------
# /api/telemetry — TelemetrySource strategy
# ---------------------------------------------------------------------------


class TestTelemetryRoute:
    """``GET /api/telemetry`` — bare path returns
    ``collect_metrics()``; ``?push`` query token triggers
    ``push_telemetry()``. Same legacy split.
    """

    def _harness_with_telemetry(
        self, source: TelemetrySource,
    ) -> RouteDispatchHarness:
        from media_stack.api.routing import (
            DefaultDispatcher,
            Router,
            RouterDispatcher,
        )

        DefaultDispatcher.reset_for_tests()
        router = Router()
        replacement = OpsGetRoutes(telemetry_source=source)
        for key, route in list(router._exact.items()):
            if route.path == "/api/telemetry":
                router._exact[key] = type(route)(
                    verb=route.verb,
                    path=route.path,
                    handler=replacement.handle_telemetry,
                    pattern=route.pattern,
                    param_names=route.param_names,
                    display=route.display,
                )
        return RouteDispatchHarness(RouterDispatcher(router))

    def test_bare_path_collects_metrics(self) -> None:
        metrics = {
            "ts": 1777140000.0,
            "cluster_id": "abc",
            "controller": {"version": "v1.2.3"},
            "services": {"total": 28, "healthy": 26, "unhealthy": 2},
        }
        source = TelemetrySource(
            collect_fn=lambda: metrics,
            push_fn=lambda: {"status": "should-not-fire"},
        )
        harness = self._harness_with_telemetry(source)

        response = harness.dispatch("GET", "/api/telemetry")

        assert response.status == 200
        assert json.loads(response.body) == metrics

    def test_push_query_routes_to_push_telemetry(self) -> None:
        push_body = {"status": "ok", "cluster_id": "abc"}
        source = TelemetrySource(
            collect_fn=lambda: {"unused": True},
            push_fn=lambda: push_body,
        )
        harness = self._harness_with_telemetry(source)

        response = _dispatch_with_query(
            harness, "GET", "/api/telemetry?push",
        )

        assert response.status == 200
        assert json.loads(response.body) == push_body

    def test_push_query_with_value_still_triggers_push(self) -> None:
        """Substring-match parity with the legacy chain: ``?push=1``,
        ``?foo=bar&push`` both count.
        """
        source = TelemetrySource(
            collect_fn=lambda: {"collected": True},
            push_fn=lambda: {"pushed": True},
        )
        harness = self._harness_with_telemetry(source)

        response = _dispatch_with_query(
            harness, "GET", "/api/telemetry?push=1",
        )
        assert json.loads(response.body) == {"pushed": True}

        response = _dispatch_with_query(
            harness, "GET", "/api/telemetry?foo=bar&push",
        )
        assert json.loads(response.body) == {"pushed": True}

    def test_unrelated_query_uses_collect_path(self) -> None:
        """A query string without the ``push`` token must still
        return the collect path — pinning so a future
        "always-push-on-any-query" regression doesn't ship.
        """
        source = TelemetrySource(
            collect_fn=lambda: {"collected": True},
            push_fn=lambda: {"pushed": True},
        )
        harness = self._harness_with_telemetry(source)

        response = _dispatch_with_query(
            harness, "GET", "/api/telemetry?nocache=1",
        )
        assert json.loads(response.body) == {"collected": True}


# ---------------------------------------------------------------------------
# Auto-discovery + spec-parity integration
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the ops
    domain. If a future change accidentally drops a handler from
    the registry, this fires before any per-route test does.
    """

    def test_all_ops_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/orchestrator/promises/state",
            "/api/runs",
            "/api/telemetry",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing ops routes: {expected - registered}"
        )

    def test_api_schedules_handled_by_misc_legacy_not_ops(self) -> None:
        """``/api/schedules`` was already migrated by
        ``misc_legacy.py``; the brief's "SKIP already-registered"
        rule means ops.py must NOT also claim it (the Router's
        startup duplicate check would fail). Pin that.
        """
        harness = RouteDispatchHarness.with_default_router()
        for r in harness._dispatcher._router.registered_routes():
            if r.path == "/api/schedules":
                assert "ops" not in r.handler.__qualname__.lower(), (
                    f"/api/schedules unexpectedly bound to {r.display}"
                )

    def test_post_to_runs_returns_method_not_allowed(self) -> None:
        from media_stack.api.routing import DispatchOutcome
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/runs")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_runs_route_uses_default_run_history_repo(self) -> None:
        """Auto-discovery wires up ``OpsGetRoutes()`` with no
        kwargs — the default ``RunHistoryRepository`` must
        construct cleanly even though it lazy-imports the real
        ``run_history`` module. Pin that the auto-wired repo
        exists and is a ``RunHistoryRepository``.
        """
        instance = OpsGetRoutes()
        assert isinstance(instance._runs, RunHistoryRepository)
        assert isinstance(instance._orchestrator, OrchestratorStateAdapter)
        assert isinstance(instance._telemetry, TelemetrySource)


# ---------------------------------------------------------------------------
# Wave-7 fakes — extend the wave-4 _FakeRunHistoryModule with the
# three additional methods that get_run_detail / get_latest_for_job call.
# ---------------------------------------------------------------------------


@dataclass
class _FakeRunHistoryModuleV2(_FakeRunHistoryModule):
    """Extends the wave-4 fake with three additional methods that the
    wave-7 ``get_run_detail`` / ``get_latest_for_job`` repository
    methods call.

    Keeps the existing ``get_runs`` / ``iter_records`` contract
    unchanged — wave-4 tests continue to pass unchanged.
    """

    def get_run(self, run_id: str) -> "_FakeRunRecord | None":
        for r in self.records:
            if r.run_id == run_id:
                return r
        return None

    def get_latest_run(self, job_name: str) -> "_FakeRunRecord | None":
        # Mirror the real impl: newest last in the JSONL → reversed scan.
        for r in reversed(self.records):
            if r.job_name == job_name:
                return r
        return None

    def get_children(self, parent_run_id: str) -> "list[_FakeRunRecord]":
        return [r for r in self.records if r.parent_run_id == parent_run_id]


# ---------------------------------------------------------------------------
# Shared harness for the two new parameterized routes
# ---------------------------------------------------------------------------


class _RunDetailHarness:
    """Rebinds all OpsGetRoutes handlers (exact + parameterized) with
    a test-wired instance so stubs flow through the production Router.
    Pattern mirrors ``_RouteHarness`` in ``test_post_jobs_queue.py``.
    """

    @classmethod
    def with_repo(cls, repo: "RunHistoryRepository") -> RouteDispatchHarness:
        from media_stack.api.routing import (
            DefaultDispatcher,
            Router,
            RouterDispatcher,
        )

        DefaultDispatcher.reset_for_tests()
        router = Router()
        replacement = OpsGetRoutes(run_history_repository=repo)
        cls._rebind(router, replacement)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Any, routes: "OpsGetRoutes") -> None:
        for key, route in list(router._exact.items()):
            m = cls._maybe_replacement(route, routes)
            if m is not None:
                router._exact[key] = type(route)(
                    verb=route.verb, path=route.path, handler=m,
                    pattern=route.pattern, param_names=route.param_names,
                    display=route.display,
                )
        for idx, route in enumerate(list(router._parameterized)):
            m = cls._maybe_replacement(route, routes)
            if m is not None:
                router._parameterized[idx] = type(route)(
                    verb=route.verb, path=route.path, handler=m,
                    pattern=route.pattern, param_names=route.param_names,
                    display=route.display,
                )

    @staticmethod
    def _maybe_replacement(route: Any, routes: "OpsGetRoutes") -> Any:
        if "OpsGetRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name, None)


# ---------------------------------------------------------------------------
# /api/runs/latest/{job_name}
# ---------------------------------------------------------------------------


class TestRunLatestRoute:
    """``GET /api/runs/latest/{job_name}`` — most-recent run for a job."""

    def _harness(
        self, fake: _FakeRunHistoryModuleV2,
    ) -> RouteDispatchHarness:
        return _RunDetailHarness.with_repo(
            RunHistoryRepository(run_history_module=fake),
        )

    def test_returns_flat_record_for_known_job(self) -> None:
        """Happy path: most-recent run returned as flat dict with no
        ``children`` key."""
        fake = _FakeRunHistoryModuleV2(records=[
            _FakeRunRecord(
                run_id="r1",
                job_name="ensure-jellyfin-libraries",
                status="ok",
                started_at=1777140000.0,
            ),
        ])
        harness = self._harness(fake)

        response = harness.dispatch(
            "GET", "/api/runs/latest/ensure-jellyfin-libraries",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["run_id"] == "r1"
        assert body["job_name"] == "ensure-jellyfin-libraries"
        assert "children" not in body

    def test_path_param_job_name_captured(self) -> None:
        """Router regex captures ``job_name`` and forwards it to the
        repository unchanged."""
        captured: list[str] = []

        fake = _FakeRunHistoryModuleV2(records=[
            _FakeRunRecord(run_id="r1", job_name="reconcile-quality-profiles"),
        ])

        class _SpyRepo(RunHistoryRepository):
            def get_latest_for_job(  # type: ignore[override]
                self, job_name: str,
            ) -> tuple[bool, dict[str, Any]]:
                captured.append(job_name)
                return super().get_latest_for_job(job_name)

        harness = _RunDetailHarness.with_repo(
            _SpyRepo(run_history_module=fake),
        )
        harness.dispatch(
            "GET", "/api/runs/latest/reconcile-quality-profiles",
        )

        assert captured == ["reconcile-quality-profiles"]

    def test_404_when_no_runs_for_job(self) -> None:
        """Legacy 404 error string preserved verbatim."""
        fake = _FakeRunHistoryModuleV2(records=[])
        harness = self._harness(fake)

        response = harness.dispatch("GET", "/api/runs/latest/never-ran")

        assert response.status == 404
        assert json.loads(response.body) == {
            "error": "no runs for 'never-ran'",
        }

    def test_parent_job_name_inlined_when_parent_resolves(self) -> None:
        """``parent_job_name`` is inlined same as in the list route."""
        fake = _FakeRunHistoryModuleV2(records=[
            _FakeRunRecord(run_id="p1", job_name="root-job"),
            _FakeRunRecord(
                run_id="c1", job_name="child-job", parent_run_id="p1",
            ),
        ])
        harness = self._harness(fake)

        response = harness.dispatch("GET", "/api/runs/latest/child-job")

        body = json.loads(response.body)
        assert body["parent_job_name"] == "root-job"


# ---------------------------------------------------------------------------
# /api/runs/{run_id}
# ---------------------------------------------------------------------------


class TestRunDetailRoute:
    """``GET /api/runs/{run_id}`` — single run with children inlined."""

    def _harness(
        self, fake: _FakeRunHistoryModuleV2,
    ) -> RouteDispatchHarness:
        return _RunDetailHarness.with_repo(
            RunHistoryRepository(run_history_module=fake),
        )

    def test_returns_record_with_children_array(self) -> None:
        """Happy path: run record merged with children array."""
        fake = _FakeRunHistoryModuleV2(records=[
            _FakeRunRecord(run_id="p1", job_name="root-job", status="ok"),
            _FakeRunRecord(
                run_id="c1", job_name="child-job", status="ok",
                parent_run_id="p1",
            ),
        ])
        harness = self._harness(fake)

        response = harness.dispatch("GET", "/api/runs/p1")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["run_id"] == "p1"
        assert body["job_name"] == "root-job"
        assert isinstance(body["children"], list)
        assert len(body["children"]) == 1
        assert body["children"][0]["run_id"] == "c1"

    def test_children_array_empty_when_no_children(self) -> None:
        """No children → ``"children": []``, not absent or None."""
        fake = _FakeRunHistoryModuleV2(records=[
            _FakeRunRecord(run_id="solo", job_name="solo-job"),
        ])
        harness = self._harness(fake)

        response = harness.dispatch("GET", "/api/runs/solo")

        body = json.loads(response.body)
        assert body["children"] == []

    def test_path_param_run_id_captured(self) -> None:
        """Router regex captures ``run_id`` and forwards it to the
        repository unchanged."""
        captured: list[str] = []

        fake = _FakeRunHistoryModuleV2(records=[
            _FakeRunRecord(run_id="run-abc123", job_name="test-job"),
        ])

        class _SpyRepo(RunHistoryRepository):
            def get_run_detail(  # type: ignore[override]
                self, run_id: str,
            ) -> tuple[bool, dict[str, Any]]:
                captured.append(run_id)
                return super().get_run_detail(run_id)

        harness = _RunDetailHarness.with_repo(
            _SpyRepo(run_history_module=fake),
        )
        harness.dispatch("GET", "/api/runs/run-abc123")

        assert captured == ["run-abc123"]

    def test_404_when_run_id_not_found(self) -> None:
        """Legacy 404 error string preserved verbatim."""
        fake = _FakeRunHistoryModuleV2(records=[])
        harness = self._harness(fake)

        response = harness.dispatch("GET", "/api/runs/nonexistent-id")

        assert response.status == 404
        assert json.loads(response.body) == {
            "error": "run 'nonexistent-id' not found",
        }

    def test_parent_job_name_inlined_on_fetched_record(self) -> None:
        """The fetched record itself carries ``parent_job_name`` when its
        parent is in the persistence window."""
        fake = _FakeRunHistoryModuleV2(records=[
            _FakeRunRecord(run_id="gp", job_name="grandparent"),
            _FakeRunRecord(
                run_id="p", job_name="parent-job", parent_run_id="gp",
            ),
            _FakeRunRecord(
                run_id="c", job_name="child-job", parent_run_id="p",
            ),
        ])
        harness = self._harness(fake)

        response = harness.dispatch("GET", "/api/runs/p")

        body = json.loads(response.body)
        assert body["parent_job_name"] == "grandparent"
        assert body["children"][0]["run_id"] == "c"
