"""Tests for ``api/routes/jobs.py`` (ADR-0007 Phase 2 wave 3).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used in
production.

Patch points:

* ``/api/jobs/queue`` delegates to ``job_queue`` imported at module
  scope on the route module — patch
  ``media_stack.api.routes.jobs.job_queue``.
* ``/api/jobs`` and ``/api/jobs/running`` use lazy imports inside
  the route methods, so we patch at the source modules
  (``application.jobs.framework`` for catalog/tree/history;
  ``application.jobs.run_history.get_running_tree`` for the
  parent → child tree).

Each route gets:

  * a happy-path test asserting the canonical body shape;
  * an empty-state test asserting the response shape pin still
    holds when the underlying data is empty (queue has no entries,
    no jobs are running, etc.);
  * a key-set assertion (``set(body.keys())``) so any future schema
    drift surfaces as a test failure rather than a silent
    backward-incompat in the SPA.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from tests.unit.api.routes._helpers import (
    MockControllerHandler,
    RouteDispatchHarness,
)


class TestJobsQueueRoute:
    """``GET /api/jobs/queue`` — operator-managed pending queue."""

    @patch("media_stack.api.routes.jobs.job_queue")
    def test_returns_queue_snapshot(self, mock_queue) -> None:
        mock_queue.get_queue.return_value = {
            "count": 2,
            "queue": [
                {
                    "id": 1777140000001,
                    "job_name": "refresh-iptv-channels",
                    "source": "manual",
                    "scheduled_at": 0,
                    "enqueued_at": 1777139999.5,
                    "label": "refresh-iptv-channels",
                },
                {
                    "id": 1777140000002,
                    "job_name": "envoy-config-rebuild",
                    "source": "config-save",
                    "scheduled_at": 0,
                    "enqueued_at": 1777140000.1,
                    "label": "envoy-config-rebuild",
                },
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/jobs/queue")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["count"] == 2
        assert len(body["queue"]) == 2
        mock_queue.get_queue.assert_called_once_with()

    @patch("media_stack.api.routes.jobs.job_queue")
    def test_returns_empty_queue(self, mock_queue) -> None:
        mock_queue.get_queue.return_value = {"count": 0, "queue": []}
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/jobs/queue")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"count": 0, "queue": []}
        assert set(body.keys()) == {"count", "queue"}


class TestJobsCatalogRoute:
    """``GET /api/jobs`` — discovered jobs + tree + run history.

    The framework symbols are imported lazily inside the route
    method, so we patch at the source module
    (``application.jobs.framework``) — the
    ``services.jobs.framework`` shim aliases its ``sys.modules``
    entry to that impl module, so a patch on either path resolves
    to the same callables.
    """

    @patch("media_stack.application.jobs.framework.get_job_history")
    @patch("media_stack.application.jobs.framework.build_job_framework")
    @patch(
        "media_stack.application.jobs.framework"
        ".discover_jobs_from_contracts",
    )
    def test_returns_jobs_catalog_with_tree_and_history(
        self, mock_discover, mock_build, mock_history,
    ) -> None:
        mock_discover.return_value = [
            {
                "name": "configure-categories",
                "phase": "download_clients",
                "priority": 10,
            },
            {
                "name": "configure-arr-clients",
                "phase": "download_clients",
                "priority": 10,
            },
        ]
        # Recursive job-tree shape: simulate root → 1 child.
        leaf = SimpleNamespace(
            name="configure-categories", requires=[], sub_jobs=[],
        )
        root = SimpleNamespace(
            name="bootstrap", requires=[], sub_jobs=[leaf],
        )
        mock_build.return_value = root
        mock_history.return_value = [
            {"job": "configure-categories", "status": "success"},
        ]
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/jobs")

        assert response.status == 200
        body = json.loads(response.body)
        # Response shape pin — UI relies on these four keys.
        assert set(body.keys()) == {"jobs", "tree", "count", "history"}
        assert body["count"] == 2
        # `tree` is a list (not a bare dict) — see v1.0.186 fix
        # note in the route module's docstring.
        assert isinstance(body["tree"], list)
        assert len(body["tree"]) == 1
        assert body["tree"][0] == {
            "name": "bootstrap",
            "requires": [],
            "sub_jobs": [
                {
                    "name": "configure-categories",
                    "requires": [],
                    "sub_jobs": [],
                },
            ],
        }
        assert body["history"] == [
            {"job": "configure-categories", "status": "success"},
        ]

    @patch("media_stack.application.jobs.framework.get_job_history")
    @patch("media_stack.application.jobs.framework.build_job_framework")
    @patch(
        "media_stack.application.jobs.framework"
        ".discover_jobs_from_contracts",
    )
    def test_returns_empty_catalog_with_root_only_tree(
        self, mock_discover, mock_build, mock_history,
    ) -> None:
        """Empty-state pin — when no jobs are discovered the route
        still emits the four canonical keys with an empty
        ``jobs[]``, ``count: 0``, an empty ``history[]``, and a
        single-root ``tree[]`` containing only the root node.
        """
        mock_discover.return_value = []
        mock_build.return_value = SimpleNamespace(
            name="bootstrap", requires=[], sub_jobs=[],
        )
        mock_history.return_value = []
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/jobs")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "jobs": [],
            "tree": [
                {"name": "bootstrap", "requires": [], "sub_jobs": []},
            ],
            "count": 0,
            "history": [],
        }


class TestJobsRunningRoute:
    """``GET /api/jobs/running`` — in-flight aggregator across
    ActionRecord rows, k8s active job pods, and the run-history
    parent → child tree.

    K8s collection is gated on ``KUBERNETES_SERVICE_HOST``; we
    leave that env var unset in tests so the k8s branch short-
    circuits cleanly. The run-history tree is patched at its
    impl module path.
    """

    @patch("media_stack.api.routes.jobs.get_running_tree")
    def test_returns_aggregator_with_action_records(
        self, mock_tree,
    ) -> None:
        mock_tree.return_value = [
            {"id": "run-42", "name": "reconcile", "children": []},
        ]
        # Build a state fixture with one currently-running action
        # plus one history row also in `running` status (re-entrant).
        running_status = SimpleNamespace(value="running")
        cur = SimpleNamespace(
            id="action-1",
            name="bootstrap",
            kind="action",
            started_at=1777140000.0,
            elapsed_seconds=12.5,
            triggered_by="cron",
            is_terminal=False,
        )
        history_row = SimpleNamespace(
            id="action-2",
            name="reconcile",
            kind="action",
            started_at=1777139900.0,
            elapsed_seconds=120.0,
            triggered_by="manual",
            is_terminal=False,
            status=running_status,
        )
        state = SimpleNamespace(
            current_action=cur,
            action_history=[cur, history_row],
        )
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/jobs/running", state=state,
        )

        assert response.status == 200
        body = json.loads(response.body)
        # Response shape pin.
        assert set(body.keys()) == {"running", "count", "tree"}
        assert body["count"] == 2
        names = {row["name"] for row in body["running"]}
        assert names == {"bootstrap", "reconcile"}
        assert body["tree"] == [
            {"id": "run-42", "name": "reconcile", "children": []},
        ]
        mock_tree.assert_called_once_with()

    @patch("media_stack.api.routes.jobs.get_running_tree")
    def test_returns_empty_aggregator_when_nothing_running(
        self, mock_tree,
    ) -> None:
        """Empty-state pin — no current action, no history rows in
        ``running`` state, no k8s, empty run-history tree. The
        route still emits the canonical shape.
        """
        mock_tree.return_value = []
        state = SimpleNamespace(current_action=None, action_history=[])
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/jobs/running", state=state,
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"running": [], "count": 0, "tree": []}

    @patch("media_stack.api.routes.jobs.get_running_tree")
    def test_running_tree_failure_degrades_to_empty_tree(
        self, mock_tree,
    ) -> None:
        """Per-source failure-mode pin — if ``get_running_tree``
        raises one of its narrowed-catch types
        (``OSError`` / ``ValueError`` / ``AttributeError``), the
        aggregator's per-source ``except`` swallows it and ``tree``
        falls back to ``[]``. The flat ``running`` list and HTTP
        200 status survive.

        ``OSError`` is the realistic failure-mode here — the run-
        history reader hits disk for the JSON-on-disk run-record
        tree.
        """
        mock_tree.side_effect = OSError("disk read failure")
        state = SimpleNamespace(current_action=None, action_history=[])
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET", "/api/jobs/running", state=state,
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"running": [], "count": 0, "tree": []}


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the Jobs
    domain. If a future change accidentally drops a handler from
    the registry, this fires before any per-route test does.
    """

    def test_all_jobs_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/jobs",
            "/api/jobs/queue",
            "/api/jobs/running",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing jobs routes: {expected - registered}"
        )

    def test_post_to_jobs_get_path_returns_method_not_allowed(
        self,
    ) -> None:
        """``/api/jobs`` is a GET-only route. Per OpenAPI the spec
        also declares ``POST /api/jobs/queue`` (enqueue), so we
        pin Method-Not-Allowed against the catalog endpoint
        instead.
        """
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/jobs")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED


class TestJobTreeBuilder:
    """``_JobTreeBuilder`` recursion — covered indirectly by the
    catalog-route happy-path test, but pinned here too so a
    regression in the recursion surfaces without the full router
    indirection.
    """

    def test_builds_recursive_tree_with_two_levels(self) -> None:
        from media_stack.api.routes.jobs import _JobTreeBuilder
        leaf = SimpleNamespace(name="leaf", requires=["parent"], sub_jobs=[])
        mid = SimpleNamespace(name="mid", requires=["root"], sub_jobs=[leaf])
        root = SimpleNamespace(name="root", requires=[], sub_jobs=[mid])

        result = _JobTreeBuilder().build(root)

        assert result == {
            "name": "root",
            "requires": [],
            "sub_jobs": [
                {
                    "name": "mid",
                    "requires": ["root"],
                    "sub_jobs": [
                        {
                            "name": "leaf",
                            "requires": ["parent"],
                            "sub_jobs": [],
                        },
                    ],
                },
            ],
        }


class TestRunningJobsAggregator:
    """``_RunningJobsAggregator`` per-source behaviour — direct
    tests of the collaborator without the full router stack.
    """

    @patch("media_stack.api.routes.jobs.get_running_tree")
    def test_action_record_failure_does_not_break_collection(
        self, mock_tree,
    ) -> None:
        """If reading the action-record state raises, the per-source
        catch swallows it and ``running`` stays empty. Tree still
        runs, k8s branch still skipped (no env var).
        """
        from media_stack.api.routes.jobs import (
            _RunningJobsAggregator,
            _RunningJobsConfig,
        )
        mock_tree.return_value = [{"id": "run-x"}]

        # state with a property that raises on access.
        # Use ``AttributeError`` since that's one of the narrowed
        # exception types ``_collect_action_records`` actually
        # catches — anything outside the (AttributeError, KeyError,
        # TypeError, ValueError) tuple is intentionally allowed to
        # propagate to the route-level outer except.
        class ExplodingState:
            @property
            def current_action(self):
                raise AttributeError("state attribute missing")

        handler = MockControllerHandler(state=ExplodingState())
        config = _RunningJobsConfig(in_kubernetes=False, namespace="x")
        running, tree = _RunningJobsAggregator(config).collect(handler)

        assert running == []
        assert tree == [{"id": "run-x"}]
