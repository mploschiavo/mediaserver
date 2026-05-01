"""Tests for the ADR-0003 Phase 1 service-lifecycle Protocol + value
types.

The Protocol is the structural contract every service adapter has to
satisfy. These tests pin:

  * ``ProbeResult`` factories build the right shape and the
    ``is_ok`` shorthand reads the literal field correctly.
  * ``Outcome[T]`` distinguishes ``success`` / ``failure`` and round-
    trips ``transient`` (the orchestrator's retry signal) faithfully.
  * ``ServiceLifecycle`` is ``runtime_checkable`` — a class missing
    one of the five methods FAILS ``isinstance`` so the orchestrator's
    Phase-2 wiring catches mis-implementations at class load instead
    of at the first invocation.
  * The Protocol is imported via the package surface
    (``domain.services``) so adapters land at the right path from day
    one. If the surface drifts, this test fails before the ratchet.

No I/O, no logging, no fixture state — pure value-type tests.
"""

from __future__ import annotations

import pytest

from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ProbeStatus,
    ServiceLifecycle,
)


class TestProbeResult:
    def test_ok_factory_sets_status_and_is_ok(self) -> None:
        r = ProbeResult.ok("everything is fine")
        assert r.status == "ok"
        assert r.is_ok is True
        assert r.detail == "everything is fine"
        assert r.evidence == {}

    def test_failed_factory_carries_detail_and_evidence(self) -> None:
        r = ProbeResult.failed(
            "no key in db",
            evidence={"key_count": 0, "queried_table": "ApiKeys"},
        )
        assert r.status == "failed"
        assert r.is_ok is False
        assert r.evidence["key_count"] == 0

    def test_unknown_factory_distinct_from_failed(self) -> None:
        # The orchestrator treats unknown the same as failed for
        # "should I run the ensurer?", but the literal status must
        # still distinguish them so operators can tell "broken" from
        # "couldn't tell".
        r = ProbeResult.unknown("network timeout")
        assert r.status == "unknown"
        assert r.is_ok is False

    def test_evaluated_at_round_trips(self) -> None:
        r = ProbeResult.ok(evaluated_at=1_700_000_000.5)
        assert r.evaluated_at == pytest.approx(1_700_000_000.5)

    def test_to_dict_is_serializable_shape(self) -> None:
        r = ProbeResult.failed("boom", evidence={"http_status": 503})
        d = r.to_dict()
        assert d == {
            "status": "failed",
            "detail": "boom",
            "evidence": {"http_status": 503},
            "evaluated_at": 0.0,
        }

    def test_frozen(self) -> None:
        # Frozen so the orchestrator can cache results across a tick
        # without worrying about mutation.
        r = ProbeResult.ok()
        with pytest.raises(Exception):
            r.detail = "mutated"  # type: ignore[misc]

    def test_status_literal_values(self) -> None:
        # ProbeStatus is a Literal of three values; nothing else.
        # Hard-pin so a future expansion doesn't drift silently.
        from typing import get_args
        assert set(get_args(ProbeStatus)) == {"ok", "failed", "unknown"}


class TestOutcome:
    def test_success_with_value(self) -> None:
        o: Outcome[str] = Outcome.success("abcd1234", attempts=2)
        assert o.ok is True
        assert o.value == "abcd1234"
        assert o.error == ""
        assert o.attempts == 2

    def test_success_with_none_value_for_void_ensurer(self) -> None:
        # persist_api_key returns Outcome[None] — success carries no
        # payload, just confirms the write landed.
        o: Outcome[None] = Outcome.success()
        assert o.ok is True
        assert o.value is None

    def test_failure_carries_error_and_transient(self) -> None:
        o: Outcome[str] = Outcome.failure(
            "service warming up", transient=True, attempts=3,
        )
        assert o.ok is False
        assert o.value is None
        assert o.error == "service warming up"
        assert o.transient is True
        assert o.attempts == 3

    def test_failure_default_transient_is_false(self) -> None:
        # Default to non-transient so a config-level failure (bad
        # credentials, wrong port) doesn't get retried forever just
        # because the orchestrator assumed retries were safe.
        o: Outcome[str] = Outcome.failure("invalid credentials")
        assert o.transient is False

    def test_to_dict_round_trips_all_fields(self) -> None:
        o: Outcome[str] = Outcome.failure(
            "boom",
            transient=True,
            attempts=2,
            elapsed_seconds=12.5,
            evidence={"last_status": 503},
        )
        d = o.to_dict()
        assert d == {
            "ok": False,
            "value": None,
            "error": "boom",
            "transient": True,
            "attempts": 2,
            "elapsed_seconds": 12.5,
            "evidence": {"last_status": 503},
        }


class TestOrchestrationContext:
    def test_defaults_are_safe_inert_values(self) -> None:
        # Frozen + safe defaults so a probe written against the
        # context can be unit-tested without setup boilerplate.
        ctx = OrchestrationContext(service_id="jellyfin")
        assert ctx.service_id == "jellyfin"
        assert ctx.config == {}
        assert ctx.secrets == {}
        assert ctx.dry_run is False
        assert ctx.is_cancelled() is False
        assert ctx.now() == 0.0

    def test_carries_config_and_secrets(self) -> None:
        ctx = OrchestrationContext(
            service_id="jellyfin",
            config={"host": "jellyfin", "port": 8096},
            secrets={"JELLYFIN_API_KEY": "abc"},
        )
        assert ctx.config["host"] == "jellyfin"
        assert ctx.secrets["JELLYFIN_API_KEY"] == "abc"

    def test_now_is_injectable_for_tests(self) -> None:
        ctx = OrchestrationContext(
            service_id="jellyfin", now=lambda: 1_700_000_000.0,
        )
        assert ctx.now() == 1_700_000_000.0

    def test_is_cancelled_is_injectable(self) -> None:
        flag = {"cancelled": False}
        ctx = OrchestrationContext(
            service_id="jellyfin",
            is_cancelled=lambda: flag["cancelled"],
        )
        assert ctx.is_cancelled() is False
        flag["cancelled"] = True
        assert ctx.is_cancelled() is True


class _CompleteLifecycle:
    """Minimal stub that satisfies the full Protocol — Phase 2 lands
    the real adapters."""

    service_id = "stub"

    def probe_running(self, ctx: OrchestrationContext) -> ProbeResult:
        return ProbeResult.ok()

    def probe_has_api_key(self, ctx: OrchestrationContext) -> ProbeResult:
        return ProbeResult.ok()

    def mint_api_key(self, ctx: OrchestrationContext) -> Outcome[str]:
        return Outcome.success("k")

    def discover_api_key(self, ctx: OrchestrationContext) -> str | None:
        return "k"

    def persist_api_key(
        self, key: str, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        return Outcome.success()


class _MissingMintLifecycle:
    """Adapter missing one method. ``runtime_checkable`` MUST flag it
    so the orchestrator's Phase-2 wiring fails fast at import."""

    service_id = "broken"

    def probe_running(self, ctx: OrchestrationContext) -> ProbeResult:
        return ProbeResult.ok()

    def probe_has_api_key(self, ctx: OrchestrationContext) -> ProbeResult:
        return ProbeResult.ok()

    # mint_api_key intentionally missing.

    def discover_api_key(self, ctx: OrchestrationContext) -> str | None:
        return None

    def persist_api_key(
        self, key: str, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        return Outcome.success()


class TestServiceLifecycleProtocol:
    def test_complete_impl_passes_isinstance(self) -> None:
        assert isinstance(_CompleteLifecycle(), ServiceLifecycle)

    def test_missing_method_fails_isinstance(self) -> None:
        # This is the load-time guarantee: Phase 2 ratchet asserts
        # every adapter passes ``isinstance(impl, ServiceLifecycle)``,
        # so a missing method blocks the merge.
        assert not isinstance(_MissingMintLifecycle(), ServiceLifecycle)

    def test_protocol_methods_invoke_through_attribute(self) -> None:
        # Sanity — calling the methods through the structural type
        # works as documented. Important because the orchestrator
        # never instantiates ServiceLifecycle directly; it walks a
        # registry of impls and calls through the Protocol surface.
        impl: ServiceLifecycle = _CompleteLifecycle()
        ctx = OrchestrationContext(service_id="stub")
        assert impl.probe_running(ctx).is_ok
        assert impl.mint_api_key(ctx).value == "k"
        assert impl.discover_api_key(ctx) == "k"
        assert impl.persist_api_key("k", ctx).ok


class TestPackageSurface:
    def test_public_names_re_exported_from_package(self) -> None:
        # Adapters import via ``from media_stack.domain.services import
        # ServiceLifecycle``. If the package __init__ drops a re-export,
        # adapters break with ImportError — better to fail here.
        from media_stack.domain import services as pkg
        for name in (
            "ServiceLifecycle",
            "ProbeResult",
            "ProbeStatus",
            "Outcome",
            "OrchestrationContext",
        ):
            assert hasattr(pkg, name), f"{name} missing from domain.services"
