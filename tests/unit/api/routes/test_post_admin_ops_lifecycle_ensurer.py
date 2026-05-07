"""Tests for ``POST /api/lifecycle-ensurers/{service}/{method}``
(ADR-0005 Phase 5b step 2).

These tests pin the new manual-dispatch surface for lifecycle
ensurers. The route is wired in ``api/routes/post_admin_ops.py``
but the business logic lives in
``api/services/lifecycle_ensurer_invoker.py`` — most assertions
target the invoker directly, with route-level assertions covering
the gate (CSRF + admin) and the wire envelope.

Coverage matrix (matches the agent prompt's checklist):

* Happy path: known ``(service, method)`` → 200, ``status="success"``.
* Unknown service → 404 + ``{"error": "unknown ensurer"}``.
* Unknown method on known service → 404.
* CSRF rejected without token → 403.
* Source tagging: body ``source="auto-heal"`` flows through into the
  ``OrchestrationContext.extra`` the dispatcher sees.
* Outcome mapping: transient failure → 200 + ``status="transient"``.
* Outcome mapping: permanent failure → 200 + ``status="permanent"``
  (NOT a 4xx — the dispatch ran).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_stack.api.routes.post_admin_ops import (
    AdminOpsPostRoutes,
    PostMutationGate,
)
from media_stack.api.services.lifecycle_ensurer_invoker import (
    LifecycleEnsurerInvocation,
    LifecycleEnsurerInvoker,
    RESPONSE_STATUS_PERMANENT,
    RESPONSE_STATUS_SUCCESS,
    RESPONSE_STATUS_TRANSIENT,
    SOURCE_AUTO_HEAL,
    SOURCE_OPERATOR,
)
from media_stack.domain.services.identifiers import (
    EnsurerMethod,
    InvocationSource,
    ServiceId,
)
from media_stack.domain.services.lifecycle import Outcome
from media_stack.domain.services.promises import (
    LifecycleEnsurer,
    LifecycleProbe,
    Promise,
)


def _inv(
    service: str,
    method: str,
    *,
    source: str = SOURCE_OPERATOR,
) -> LifecycleEnsurerInvocation:
    """Tiny test helper — wrap (service, method, source) into a
    ``LifecycleEnsurerInvocation`` value object so tests aren't
    forced to repeat the dataclass construction at every call site.
    """
    return LifecycleEnsurerInvocation(
        service=ServiceId(service),
        method=EnsurerMethod(method),
        source=InvocationSource(source),
    )
from tests.unit.api.routes.test_post_admin_ops import (
    _AlwaysAllowGate,
    _RouteHarness,
    _dispatch_post,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _promise(
    pid: str, *, service: str, method: str,
) -> Promise:
    """Build a ``Promise`` whose ensurer is a ``LifecycleEnsurer``.

    The probe field is required by ``Promise``'s constructor but
    isn't relevant to these tests — the invoker only reads the
    ``ensurer.service`` / ``ensurer.method`` fields off each
    promise.
    """
    return Promise(
        id=pid,
        description=f"test promise {pid}",
        platforms=("k8s", "compose"),
        probe=LifecycleProbe(service=service, method="probe_running"),
        ensurer=LifecycleEnsurer(service=service, method=method),
    )


def _registry_with(
    *pairs: tuple[str, str],
) -> list[Promise]:
    return [
        _promise(f"p_{svc}_{m}", service=svc, method=m)
        for svc, m in pairs
    ]


def _make_invoker(
    *,
    pairs: tuple[tuple[str, str], ...] = (
        ("jellyfin", "mint_api_key"),
        ("sonarr", "mint_api_key"),
    ),
    outcome: Outcome[Any] = Outcome.success(
        None, evidence={"reason": "minted"},
    ),
    capture: dict[str, Any] | None = None,
) -> LifecycleEnsurerInvoker:
    """Build a ``LifecycleEnsurerInvoker`` whose dispatcher returns
    a controlled outcome. ``capture`` (if passed) records the
    arguments the dispatcher saw — tests use it to assert source
    tagging flowed through into the OrchestrationContext.
    """
    captured = capture if capture is not None else {}

    def fake_dispatch(
        spec: LifecycleEnsurer,
        *,
        resolver: Any,
        now: float,
        secrets: Any,
    ) -> Outcome[Any]:
        # Build the context the way the real dispatcher does so the
        # source-tagging wrapper actually fires.
        ctx = resolver.context_for(
            spec.service, secrets=secrets, now_fn=lambda: now,
        )
        captured["spec"] = spec
        captured["ctx_extra"] = dict(ctx.extra)
        captured["secrets"] = dict(secrets or {})
        captured["now"] = now
        return outcome

    fake_resolver = MagicMock()
    fake_resolver.context_for.side_effect = (
        lambda service_id, *, secrets=None, now_fn=None: _StubContext(
            service_id=service_id,
            secrets=dict(secrets or {}),
            now=now_fn or (lambda: 0.0),
        )
    )
    fake_resolver.resolve.return_value = MagicMock()

    return LifecycleEnsurerInvoker(
        resolver=fake_resolver,
        registry_loader=lambda: _registry_with(*pairs),
        dispatch_fn=fake_dispatch,
        clock=lambda: 1234.0,
        secrets_resolver=lambda svc: {f"{svc.upper()}_API_KEY": "abc"},
    )


class _StubContext:
    """Lightweight ``OrchestrationContext`` stand-in.

    The wrapping ``_SourceTaggingResolver`` rebuilds an
    ``OrchestrationContext`` from this stub's fields, so we only
    need to mirror the attribute surface the wrapper reads.
    """

    def __init__(
        self, *, service_id: str, secrets: dict[str, str], now: Any,
    ) -> None:
        self.service_id = service_id
        self.config = {"host": "h"}
        self.secrets = dict(secrets)
        self.now = now
        self.is_cancelled = lambda: False
        self.dry_run = False
        self.extra: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Direct invoker tests
# ---------------------------------------------------------------------------


class TestLifecycleEnsurerInvokerDirect:
    """Exercise the service in isolation — no routing harness.
    Cheaper to run, easier to read, every dispatch arg observable.
    """

    def test_known_pair_success(self) -> None:
        invoker = _make_invoker()
        status, body = invoker.invoke(
            _inv("jellyfin", "mint_api_key"),
        )
        assert status == 200
        assert body["status"] == RESPONSE_STATUS_SUCCESS
        assert body["source"] == SOURCE_OPERATOR
        assert body["evidence"] == {"reason": "minted"}
        assert "message" in body

    def test_unknown_service_returns_404(self) -> None:
        invoker = _make_invoker()
        status, body = invoker.invoke(
            _inv("ghost-svc", "mint_api_key"),
        )
        assert status == 404
        assert body == {
            "error": "unknown ensurer",
            "service": "ghost-svc",
            "method": "mint_api_key",
        }

    def test_unknown_method_on_known_service_returns_404(self) -> None:
        invoker = _make_invoker()
        status, body = invoker.invoke(
            _inv("jellyfin", "delete_everything"),
        )
        assert status == 404
        assert body["error"] == "unknown ensurer"
        assert body["service"] == "jellyfin"
        assert body["method"] == "delete_everything"

    def test_source_tag_flows_through_to_context(self) -> None:
        capture: dict[str, Any] = {}
        invoker = _make_invoker(capture=capture)
        invoker.invoke(
            _inv("jellyfin", "mint_api_key", source=SOURCE_AUTO_HEAL),
        )
        assert capture["ctx_extra"]["invocation_source"] == SOURCE_AUTO_HEAL

    def test_default_source_when_caller_omits(self) -> None:
        capture: dict[str, Any] = {}
        invoker = _make_invoker(capture=capture)
        # Pass empty string — same shape the route does when body
        # has no ``source`` key.
        _, body = invoker.invoke(
            _inv("jellyfin", "mint_api_key", source=""),
        )
        assert body["source"] == SOURCE_OPERATOR
        assert capture["ctx_extra"]["invocation_source"] == SOURCE_OPERATOR

    def test_unknown_source_falls_back_to_operator(self) -> None:
        """Source is observability-only; an unrecognized value
        defaults to ``operator`` rather than 400-ing the request.
        Pinned so a slightly off UI tag (typo, casing) doesn't
        break the dispatch.
        """
        capture: dict[str, Any] = {}
        invoker = _make_invoker(capture=capture)
        _, body = invoker.invoke(
            _inv("jellyfin", "mint_api_key", source="DASHBOARD"),
        )
        assert body["source"] == SOURCE_OPERATOR
        assert capture["ctx_extra"]["invocation_source"] == SOURCE_OPERATOR

    def test_transient_outcome_maps_to_200_transient(self) -> None:
        invoker = _make_invoker(
            outcome=Outcome.failure(
                "service warming up", transient=True,
                evidence={"http_status": 503},
            ),
        )
        status, body = invoker.invoke(
            _inv("jellyfin", "mint_api_key"),
        )
        assert status == 200
        assert body["status"] == RESPONSE_STATUS_TRANSIENT
        assert body["message"] == "service warming up"
        assert body["evidence"] == {"http_status": 503}

    def test_permanent_outcome_maps_to_200_permanent(self) -> None:
        """The dispatch RAN — the response is 200. Operators see
        ``status="permanent"`` and decide to intervene.
        """
        invoker = _make_invoker(
            outcome=Outcome.failure(
                "bad credentials", transient=False,
            ),
        )
        status, body = invoker.invoke(
            _inv("jellyfin", "mint_api_key"),
        )
        assert status == 200
        assert body["status"] == RESPONSE_STATUS_PERMANENT
        assert body["message"] == "bad credentials"

    def test_secrets_resolver_called_with_service_id(self) -> None:
        capture: dict[str, Any] = {}
        invoker = _make_invoker(capture=capture)
        invoker.invoke(
            _inv("sonarr", "mint_api_key"),
        )
        assert capture["secrets"] == {"SONARR_API_KEY": "abc"}

    def test_dispatch_receives_lifecycle_ensurer_with_pair(self) -> None:
        capture: dict[str, Any] = {}
        invoker = _make_invoker(capture=capture)
        invoker.invoke(
            _inv("jellyfin", "mint_api_key"),
        )
        spec = capture["spec"]
        assert isinstance(spec, LifecycleEnsurer)
        assert spec.service == "jellyfin"
        assert spec.method == "mint_api_key"

    def test_clock_used_for_dispatch_now_arg(self) -> None:
        capture: dict[str, Any] = {}
        invoker = _make_invoker(capture=capture)
        invoker.invoke(
            _inv("jellyfin", "mint_api_key"),
        )
        assert capture["now"] == 1234.0

    def test_registry_load_failure_treated_as_unknown(self) -> None:
        """If ``load_registry`` raises, the invoker logs + returns a
        404 instead of a 500. The controller is already broken in
        deeper ways at that point; better to give the caller a
        deterministic answer than to crash the request.
        """
        def boom() -> list[Promise]:
            raise RuntimeError("registry yaml malformed")

        invoker = LifecycleEnsurerInvoker(
            resolver=MagicMock(),
            registry_loader=boom,
            dispatch_fn=lambda *a, **k: pytest.fail("should not dispatch"),
        )
        status, body = invoker.invoke(
            _inv("jellyfin", "mint_api_key"),
        )
        assert status == 404
        assert body["error"] == "unknown ensurer"


# ---------------------------------------------------------------------------
# Route-level tests (gate + wire envelope)
# ---------------------------------------------------------------------------


def _routes_with_invoker(
    invoker: LifecycleEnsurerInvoker,
    *,
    gate: Any | None = None,
) -> AdminOpsPostRoutes:
    return AdminOpsPostRoutes(
        mutation_gate=gate or _AlwaysAllowGate(),
        lifecycle_invoker=invoker,
    )


class TestLifecycleEnsurerRouteWiring:
    """End-to-end via the production Router: the route module is
    rebound with a stub invoker, then dispatched through the real
    parameterized-path matching code so URL parsing + body parsing
    are covered.
    """

    def test_known_pair_returns_200_with_envelope(self) -> None:
        invoker = _make_invoker()
        routes = _routes_with_invoker(invoker)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness,
            "/api/lifecycle-ensurers/jellyfin/mint_api_key",
            body={"source": SOURCE_OPERATOR},
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == RESPONSE_STATUS_SUCCESS
        assert body["source"] == SOURCE_OPERATOR

    def test_unknown_pair_returns_404(self) -> None:
        invoker = _make_invoker()
        routes = _routes_with_invoker(invoker)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness,
            "/api/lifecycle-ensurers/no-such-svc/no-such-method",
            body={"source": SOURCE_OPERATOR},
        )

        assert response.status == 404
        assert json.loads(response.body) == {
            "error": "unknown ensurer",
            "service": "no-such-svc",
            "method": "no-such-method",
        }

    def test_body_source_auto_heal_propagates(self) -> None:
        capture: dict[str, Any] = {}
        invoker = _make_invoker(capture=capture)
        routes = _routes_with_invoker(invoker)
        harness = _RouteHarness.with_routes(routes)

        _dispatch_post(
            harness,
            "/api/lifecycle-ensurers/jellyfin/mint_api_key",
            body={"source": SOURCE_AUTO_HEAL},
        )
        assert capture["ctx_extra"]["invocation_source"] == SOURCE_AUTO_HEAL

    def test_missing_body_defaults_to_operator(self) -> None:
        capture: dict[str, Any] = {}
        invoker = _make_invoker(capture=capture)
        routes = _routes_with_invoker(invoker)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness,
            "/api/lifecycle-ensurers/jellyfin/mint_api_key",
            body=b"",
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["source"] == SOURCE_OPERATOR
        assert capture["ctx_extra"]["invocation_source"] == SOURCE_OPERATOR

    def test_transient_outcome_returns_200(self) -> None:
        invoker = _make_invoker(
            outcome=Outcome.failure(
                "warming up", transient=True,
            ),
        )
        routes = _routes_with_invoker(invoker)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness,
            "/api/lifecycle-ensurers/jellyfin/mint_api_key",
            body={"source": SOURCE_OPERATOR},
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == RESPONSE_STATUS_TRANSIENT

    def test_permanent_outcome_returns_200(self) -> None:
        invoker = _make_invoker(
            outcome=Outcome.failure(
                "bad creds", transient=False,
            ),
        )
        routes = _routes_with_invoker(invoker)
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness,
            "/api/lifecycle-ensurers/jellyfin/mint_api_key",
            body={"source": SOURCE_OPERATOR},
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == RESPONSE_STATUS_PERMANENT


class TestLifecycleEnsurerRouteCsrfGate:
    """Pin that the migrated POST route enforces CSRF the same way
    every other admin-ops POST does.

    The legacy chain ran ``_global_preflight`` before any handler
    body — but Router-dispatched routes bypass that gate. The
    ``PostMutationGate`` in this module is the only line of defence;
    a regression here re-opens the CSRF surface for this endpoint.
    """

    def test_csrf_rejected_without_token(self) -> None:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        gate = PostMutationGate(csrf=csrf_stub)
        capture: dict[str, Any] = {}
        invoker = _make_invoker(capture=capture)
        routes = AdminOpsPostRoutes(
            mutation_gate=gate, lifecycle_invoker=invoker,
        )
        harness = _RouteHarness.with_routes(routes)

        response = _dispatch_post(
            harness,
            "/api/lifecycle-ensurers/jellyfin/mint_api_key",
            body={"source": SOURCE_OPERATOR},
            headers={"Cookie": "media_stack_csrf=abc"},
        )

        assert response.status == 403
        # Invoker MUST not be reached when CSRF rejects.
        assert capture == {}
