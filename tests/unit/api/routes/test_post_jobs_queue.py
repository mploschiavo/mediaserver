"""Tests for ``api/routes/post_jobs_queue.py`` (ADR-0007 Phase 2 wave 6).

Covers the four operator-job-queue POST routes lifted off the
legacy ``handlers_post`` elif chain. The legacy chain's behaviour
is the contract — these tests pin the wire shape (validation,
error envelopes, service-call wiring) at the route boundary so a
later refactor of ``services/job_queue.py`` doesn't accidentally
regress the dashboard's queue panel.

Test layout mirrors ``test_post_admin_ops`` (same wave's template):

* Per-route ``Test<Domain>Route`` classes.
* ``_RouteHarness`` rebinds the auto-discovered
  ``JobsQueuePostRoutes`` with a test-wired instance so stubs flow
  through the production Router.
* End-of-file ``TestPostMutationGate``-style class pins CSRF
  short-circuits + 403 emission, plus ``TestRoutingIntegration``
  pins auto-discovery + spec-parity.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_jobs_queue import (
    JobIdResolver,
    JobQueueRepository,
    JobsQueuePostRoutes,
    QueueClearService,
    ReorderRequestParser,
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
# POST-aware mock handler — lifted from test_post_admin_ops verbatim
# so the wave-6 tests don't take a runtime import dep on the
# wave-5 test file.
# ---------------------------------------------------------------------------


class PostMockHandler(MockControllerHandler):
    """``MockControllerHandler`` extended with a ``_read_json_body``
    that mirrors the production server's behaviour.

    Reads ``Content-Length`` from the request headers, slices that
    many bytes off ``self.rfile``, and ``json.loads`` the result.
    Empty/missing length returns ``{}`` — same shape the real
    handler returns when the body is absent or malformed.
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
    """Permissive ``PostMutationGate`` stand-in for tests focused
    on business logic.

    Implements the gate's surface (``verify`` / ``reject``) so
    tests can exercise routes without forging CSRF tokens.
    """

    def verify(self, handler: Any) -> bool:
        return True

    def reject(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError(
            "_AlwaysAllowGate.reject called — verify should have "
            "returned True",
        )


# ---------------------------------------------------------------------------
# Harness — rebinds the auto-discovered JobsQueuePostRoutes with a
# test-wired instance so stubs flow through the production Router.
# Pattern mirrors test_post_admin_ops::_RouteHarness.
# ---------------------------------------------------------------------------


class _RouteHarness:
    @classmethod
    def with_routes(
        cls, routes: JobsQueuePostRoutes,
    ) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Router, routes: JobsQueuePostRoutes) -> None:
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
    def _maybe_replacement(route: Any, routes: JobsQueuePostRoutes) -> Any:
        if "JobsQueuePostRoutes" not in route.display:
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


def _routes_with(**kwargs: Any) -> JobsQueuePostRoutes:
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    return JobsQueuePostRoutes(**kwargs)


# ---------------------------------------------------------------------------
# /api/jobs/queue — JobQueueRepository.enqueue
# ---------------------------------------------------------------------------


class TestEnqueueRoute:
    def test_forwards_full_body_to_repository(self) -> None:
        captured: dict[str, Any] = {}

        def fake_enqueue(
            job_name: str, *,
            source: str, scheduled_at: float, label: str,
        ) -> dict[str, Any]:
            captured["job_name"] = job_name
            captured["source"] = source
            captured["scheduled_at"] = scheduled_at
            captured["label"] = label
            return {
                "status": "queued",
                "entry": {
                    "id": 1, "job_name": job_name, "source": source,
                    "label": label, "scheduled_at": scheduled_at,
                },
            }

        routes = _routes_with(
            queue_repository=JobQueueRepository(enqueue_fn=fake_enqueue),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/jobs/queue",
            body={
                "job_name": "refresh-iptv-channels",
                "source": "manual",
                "scheduled_at": 1777140000,
                "label": "refresh IPTV channels",
            },
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "queued"
        assert body["entry"]["job_name"] == "refresh-iptv-channels"
        assert captured == {
            "job_name": "refresh-iptv-channels",
            "source": "manual",
            "scheduled_at": 1777140000.0,
            "label": "refresh IPTV channels",
        }

    def test_defaults_source_to_manual_when_omitted(self) -> None:
        captured: dict[str, Any] = {}

        def fake_enqueue(
            job_name: str, *,
            source: str, scheduled_at: float, label: str,
        ) -> dict[str, Any]:
            captured["source"] = source
            captured["scheduled_at"] = scheduled_at
            captured["label"] = label
            return {"status": "queued"}

        routes = _routes_with(
            queue_repository=JobQueueRepository(enqueue_fn=fake_enqueue),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/api/jobs/queue",
            body={"job_name": "envoy-config-rebuild"},
        )

        assert captured["source"] == "manual"
        # Missing/falsy ``scheduled_at`` collapses to 0.0 — same
        # semantic the legacy chain enforced.
        assert captured["scheduled_at"] == 0.0
        # Missing label is empty string — service layer fills the
        # operator-facing display from ``job_name``.
        assert captured["label"] == ""

    def test_empty_body_forwards_empty_job_name(self) -> None:
        """When the body is missing, the service layer's
        validation rejects the empty ``job_name`` with an error
        envelope. Pin that the route doesn't pre-empt that check.
        """
        captured: dict[str, Any] = {}

        def fake_enqueue(
            job_name: str, **_: Any,
        ) -> dict[str, Any]:
            captured["job_name"] = job_name
            return {"error": "job_name is required"}

        routes = _routes_with(
            queue_repository=JobQueueRepository(enqueue_fn=fake_enqueue),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/jobs/queue", body=b"")

        assert response.status == 200
        assert json.loads(response.body) == {"error": "job_name is required"}
        assert captured["job_name"] == ""

    def test_falsy_scheduled_at_collapses_to_zero(self) -> None:
        """``scheduled_at: null`` / ``0`` / missing all flow as
        ``0.0`` — pin the ``or 0`` short-circuit so a future
        refactor doesn't accidentally start passing ``None`` to
        ``float()``."""
        captured: dict[str, Any] = {}

        def fake_enqueue(
            job_name: str, *,
            source: str, scheduled_at: float, label: str,
        ) -> dict[str, Any]:
            captured["scheduled_at"] = scheduled_at
            return {"status": "queued"}

        routes = _routes_with(
            queue_repository=JobQueueRepository(enqueue_fn=fake_enqueue),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/api/jobs/queue",
            body={"job_name": "noop", "scheduled_at": None},
        )

        assert captured["scheduled_at"] == 0.0


# ---------------------------------------------------------------------------
# /api/jobs/queue/clear — QueueClearService
# ---------------------------------------------------------------------------


class TestClearRoute:
    def test_returns_clear_result(self) -> None:
        clear_payload = {"status": "cleared", "count": 3}
        routes = _routes_with(
            clear_service=QueueClearService(clear_fn=lambda: clear_payload),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/jobs/queue/clear")

        assert response.status == 200
        assert json.loads(response.body) == clear_payload

    def test_clear_called_with_no_args(self) -> None:
        calls: list[Any] = []

        def fake_clear() -> dict[str, Any]:
            calls.append(True)
            return {"status": "cleared", "count": 0}

        routes = _routes_with(
            clear_service=QueueClearService(clear_fn=fake_clear),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(harness, "/api/jobs/queue/clear")

        assert calls == [True]


# ---------------------------------------------------------------------------
# /api/jobs/queue/{entry_id}/remove — JobQueueRepository.remove
# ---------------------------------------------------------------------------


class TestRemoveRoute:
    def test_valid_id_forwards_to_remove(self) -> None:
        captured: dict[str, Any] = {}

        def fake_remove(entry_id: int) -> dict[str, Any]:
            captured["entry_id"] = entry_id
            return {"status": "removed", "id": entry_id}

        routes = _routes_with(
            queue_repository=JobQueueRepository(remove_fn=fake_remove),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/jobs/queue/1777140000001/remove",
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "status": "removed", "id": 1777140000001,
        }
        assert captured["entry_id"] == 1777140000001
        # Pin that the parsed param is a real Python int — not a
        # str the URL parser yielded — so downstream comparisons
        # against persisted ``id`` ints don't silently fall through.
        assert isinstance(captured["entry_id"], int)

    def test_non_integer_id_returns_400(self) -> None:
        routes = _routes_with(
            queue_repository=JobQueueRepository(
                remove_fn=lambda eid: pytest.fail("should not call remove"),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/jobs/queue/not-a-number/remove",
        )

        assert response.status == 400
        assert json.loads(response.body) == {
            "error": "Invalid queue entry ID",
        }

    def test_unknown_id_returns_service_error_envelope(self) -> None:
        """Unknown ids return 200 + ``{error: ...}`` — same shape
        the legacy chain emits (the service layer's "not found"
        rides the success status code).
        """
        routes = _routes_with(
            queue_repository=JobQueueRepository(
                remove_fn=lambda eid: {"error": f"queue entry {eid} not found"},
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(harness, "/api/jobs/queue/9999/remove")

        assert response.status == 200
        assert json.loads(response.body) == {
            "error": "queue entry 9999 not found",
        }


# ---------------------------------------------------------------------------
# /api/jobs/queue/{entry_id}/reorder — JobQueueRepository.reorder
# ---------------------------------------------------------------------------


class TestReorderRoute:
    def test_direction_up_forwarded(self) -> None:
        captured: dict[str, Any] = {}

        def fake_reorder(entry_id: int, **kwargs: Any) -> dict[str, Any]:
            captured["entry_id"] = entry_id
            captured["kwargs"] = kwargs
            return {"status": "reordered", "id": entry_id, "position": 0}

        routes = _routes_with(
            queue_repository=JobQueueRepository(reorder_fn=fake_reorder),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/jobs/queue/42/reorder",
            body={"direction": "up"},
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "status": "reordered", "id": 42, "position": 0,
        }
        assert captured == {"entry_id": 42, "kwargs": {"direction": "up"}}

    def test_position_forwarded_as_int(self) -> None:
        captured: dict[str, Any] = {}

        def fake_reorder(entry_id: int, **kwargs: Any) -> dict[str, Any]:
            captured["kwargs"] = kwargs
            return {"status": "reordered"}

        routes = _routes_with(
            queue_repository=JobQueueRepository(reorder_fn=fake_reorder),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/api/jobs/queue/42/reorder",
            body={"position": "3"},
        )

        # Body's ``position`` is a string in the test request but
        # the parser coerces to int before forwarding.
        assert captured["kwargs"] == {"position": 3}

    def test_omits_unspecified_keys(self) -> None:
        """Body with neither ``direction`` nor ``position``
        forwards an empty kwargs dict — the service layer's
        "direction or position required" error envelope handles
        the validation. Mirrors legacy "only forward keys body
        actually supplied" semantic.
        """
        captured: dict[str, Any] = {}

        def fake_reorder(entry_id: int, **kwargs: Any) -> dict[str, Any]:
            captured["kwargs"] = kwargs
            return {"error": "direction or position is required"}

        routes = _routes_with(
            queue_repository=JobQueueRepository(reorder_fn=fake_reorder),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(harness, "/api/jobs/queue/42/reorder", body={})

        assert captured["kwargs"] == {}

    def test_non_integer_id_returns_400(self) -> None:
        routes = _routes_with(
            queue_repository=JobQueueRepository(
                reorder_fn=lambda eid, **_: pytest.fail(
                    "should not call reorder",
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/jobs/queue/abc/reorder",
            body={"direction": "up"},
        )

        assert response.status == 400


# ---------------------------------------------------------------------------
# Strategy unit coverage — JobIdResolver / ReorderRequestParser /
# default-path collaborator wiring
# ---------------------------------------------------------------------------


class TestJobIdResolver:
    def test_int_string_parses(self) -> None:
        parsed, error = JobIdResolver().parse("42")
        assert parsed == 42
        assert error is None

    def test_real_int_parses(self) -> None:
        parsed, error = JobIdResolver().parse(7)
        assert parsed == 7
        assert error is None

    def test_non_numeric_returns_error_envelope(self) -> None:
        parsed, error = JobIdResolver().parse("abc")
        assert parsed is None
        assert error == {"error": "Invalid queue entry ID"}

    def test_none_returns_error_envelope(self) -> None:
        parsed, error = JobIdResolver().parse(None)
        assert parsed is None
        assert error == {"error": "Invalid queue entry ID"}


class TestReorderRequestParser:
    def test_empty_body_yields_empty_kwargs(self) -> None:
        assert ReorderRequestParser().build({}) == {}

    def test_direction_only(self) -> None:
        assert ReorderRequestParser().build({"direction": "down"}) == {
            "direction": "down",
        }

    def test_position_coerced_to_int(self) -> None:
        assert ReorderRequestParser().build({"position": "5"}) == {
            "position": 5,
        }

    def test_position_passthrough_when_uncoercible(self) -> None:
        """Non-int ``position`` flows through verbatim — the
        service layer's validator emits the canonical error
        envelope, mirroring legacy chain behaviour."""
        result = ReorderRequestParser().build({"position": "not-a-num"})
        assert result == {"position": "not-a-num"}

    def test_both_keys_forwarded(self) -> None:
        result = ReorderRequestParser().build(
            {"direction": "up", "position": 2},
        )
        assert result == {"direction": "up", "position": 2}


class TestRepositoryDefaultPath:
    """Pin that ``JobQueueRepository`` does a fresh module-attribute
    lookup on each call when no constructor-injected callable is
    supplied — avoids the lazy-cache resolver shape that earlier
    waves had to retro-clean.
    """

    def test_enqueue_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import job_queue as job_queue_svc

        calls: list[tuple[Any, dict[str, Any]]] = []

        def stub_enqueue(job_name: str, **kwargs: Any) -> dict[str, Any]:
            calls.append((job_name, kwargs))
            return {"status": "queued"}

        monkeypatch.setattr(job_queue_svc, "enqueue", stub_enqueue)

        repo = JobQueueRepository()
        result = repo.enqueue(
            "test-job", source="manual", scheduled_at=0.0, label="x",
        )
        assert result == {"status": "queued"}
        assert calls == [
            ("test-job", {
                "source": "manual", "scheduled_at": 0.0, "label": "x",
            }),
        ]

    def test_remove_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import job_queue as job_queue_svc

        monkeypatch.setattr(
            job_queue_svc, "remove_entry",
            lambda eid: {"status": "removed", "id": eid},
        )

        repo = JobQueueRepository()
        assert repo.remove(7) == {"status": "removed", "id": 7}

    def test_reorder_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import job_queue as job_queue_svc

        captured: dict[str, Any] = {}

        def stub_reorder(eid: int, **kwargs: Any) -> dict[str, Any]:
            captured["eid"] = eid
            captured["kwargs"] = kwargs
            return {"status": "reordered"}

        monkeypatch.setattr(job_queue_svc, "reorder_entry", stub_reorder)

        repo = JobQueueRepository()
        result = repo.reorder(11, direction="up")
        assert result == {"status": "reordered"}
        assert captured == {"eid": 11, "kwargs": {"direction": "up"}}

    def test_clear_default_path_uses_fresh_lookup(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import job_queue as job_queue_svc

        monkeypatch.setattr(
            job_queue_svc, "clear_queue",
            lambda: {"status": "cleared", "count": 5},
        )

        svc = QueueClearService()
        assert svc.clear() == {"status": "cleared", "count": 5}

    def test_repository_does_not_cache_default_resolution(
        self, monkeypatch,
    ) -> None:
        """Critical anti-pattern guard: the default-path lookup
        must be FRESH on each call. If the repository cached the
        first-resolved callable, a later ``monkeypatch.setattr``
        wouldn't take effect — exactly the bug ADR-0007 wave-3+4
        retros eliminated. Pin that call N+1 sees the patch from
        between call N and call N+1.
        """
        from media_stack.api.services import job_queue as job_queue_svc

        repo = JobQueueRepository()

        monkeypatch.setattr(
            job_queue_svc, "clear_queue",
            lambda: {"status": "first"},
        )
        # First call binds nothing — there's no cache.
        first = QueueClearService().clear()
        assert first == {"status": "first"}

        # Re-patch and call again on the SAME repo — must see the
        # new attribute, not a cached fn from the first call.
        monkeypatch.setattr(
            job_queue_svc, "clear_queue",
            lambda: {"status": "second"},
        )
        second = QueueClearService().clear()
        assert second == {"status": "second"}

        # Same idea on the JobQueueRepository default path.
        monkeypatch.setattr(
            job_queue_svc, "remove_entry",
            lambda eid: {"status": "first-remove", "id": eid},
        )
        assert repo.remove(1) == {"status": "first-remove", "id": 1}
        monkeypatch.setattr(
            job_queue_svc, "remove_entry",
            lambda eid: {"status": "second-remove", "id": eid},
        )
        assert repo.remove(2) == {"status": "second-remove", "id": 2}


# ---------------------------------------------------------------------------
# CSRF gate — security regression coverage
# ---------------------------------------------------------------------------


class TestCsrfGate:
    """Pin the CSRF double-submit semantics for the migrated
    operator-queue routes. The legacy chain enforced this in
    ``_global_preflight``; the Router-dispatched path bypasses
    that, so the gate is the only line of defence — regressions
    here would re-open the CSRF surface for queue mutations.
    """

    def test_route_blocks_when_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        called: list[Any] = []

        def boom() -> dict[str, Any]:
            called.append(True)
            return {}

        routes = JobsQueuePostRoutes(
            mutation_gate=gate,
            clear_service=QueueClearService(clear_fn=boom),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/jobs/queue/clear",
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert called == []

    def test_enqueue_blocked_when_gate_rejects(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        enqueue_calls: list[Any] = []

        routes = JobsQueuePostRoutes(
            mutation_gate=gate,
            queue_repository=JobQueueRepository(
                enqueue_fn=lambda *a, **k: (
                    enqueue_calls.append((a, k)) or {"status": "queued"}
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response = _dispatch_post(
            harness, "/api/jobs/queue",
            body={"job_name": "x"},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert enqueue_calls == []


# ---------------------------------------------------------------------------
# Routing integration — auto-discovery + spec-parity
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity for the operator-queue
    POST domain. If a future change accidentally drops a handler,
    this fires before any per-route test does.
    """

    _EXPECTED = frozenset({
        "/api/jobs/queue",
        "/api/jobs/queue/clear",
        "/api/jobs/queue/{entry_id}/remove",
        "/api/jobs/queue/{entry_id}/reorder",
    })

    def test_all_jobs_queue_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST"
            and "JobsQueuePostRoutes" in r.display
        }
        assert registered == self._EXPECTED, (
            f"Mismatch — missing: {self._EXPECTED - registered}, "
            f"unexpected: {registered - self._EXPECTED}"
        )

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = JobsQueuePostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(instance._repo, JobQueueRepository)
        assert isinstance(instance._clear, QueueClearService)
        assert isinstance(instance._id_resolver, JobIdResolver)
        assert isinstance(instance._reorder_parser, ReorderRequestParser)

    def test_get_to_post_only_path_returns_method_not_allowed(self) -> None:
        """``/api/jobs/queue/clear`` is POST-only in the spec; a
        GET should land on METHOD_NOT_ALLOWED, not on the legacy
        chain. Pins the dispatcher's verb-discrimination.
        """
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("GET", "/api/jobs/queue/clear")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
