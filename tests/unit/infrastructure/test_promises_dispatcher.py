"""Tests for ``infrastructure.promises.dispatcher``.

Pin: each probe + ensurer kind dispatches to the right handler;
errors become uniform ``ProbeResult.unknown`` / ``Outcome.failure``
shapes (probes never raise, ensurers report transient vs permanent).
"""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_stack.domain.services.lifecycle import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ServiceLifecycle,
)
from media_stack.domain.services.promises import (
    DeployEnsurer,
    FileJsonProbe,
    FileTextProbe,
    HttpJsonProbe,
    HttpStatusProbe,
    InfraEnsurer,
    JobEnsurer,
    LifecycleEnsurer,
    LifecycleProbe,
    K8sExecProbe,
    K8sResourceProbe,
)
from media_stack.infrastructure.promises.dispatcher import (
    LifecycleResolver,
    dispatch_ensurer,
    dispatch_probe,
)


# --- Fixtures ---------------------------------------------------------


class _FakeLifecycle:
    service_id = "fake"

    def __init__(
        self, probe_running: ProbeResult | None = None,
        probe_has_api_key: ProbeResult | None = None,
        mint_outcome: Outcome | None = None,
    ) -> None:
        self._probe_running = probe_running or ProbeResult.ok("fake up")
        self._probe_has_api_key = probe_has_api_key or ProbeResult.ok()
        self._mint_outcome = mint_outcome or Outcome.success("fake-key")

    def probe_running(self, ctx: OrchestrationContext) -> ProbeResult:
        return self._probe_running

    def probe_has_api_key(self, ctx: OrchestrationContext) -> ProbeResult:
        return self._probe_has_api_key

    def mint_api_key(self, ctx: OrchestrationContext) -> Outcome[str]:
        return self._mint_outcome

    def discover_api_key(self, ctx: OrchestrationContext) -> str | None:
        return None

    def persist_api_key(
        self, key: str, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        return Outcome.success()


class _StubResolver(LifecycleResolver):
    """LifecycleResolver that returns hand-rolled lifecycle instances
    + service configs without touching the contracts/services dir."""

    def __init__(
        self,
        impls: dict[str, ServiceLifecycle] | None = None,
        configs: dict[str, dict] | None = None,
    ) -> None:
        super().__init__(contracts_dir=Path("/nonexistent"))
        self._impls_stub = impls or {}
        self._configs_stub = configs or {}

    def resolve(self, service_id: str) -> ServiceLifecycle | None:
        return self._impls_stub.get(service_id)

    def read_service_config(self, service_id: str) -> dict:
        return dict(self._configs_stub.get(service_id, {}))


# --- Lifecycle probe + ensurer ---------------------------------------


class TestLifecycleProbe:
    def test_returns_lifecycle_method_result(self) -> None:
        impl = _FakeLifecycle(probe_running=ProbeResult.ok("up"))
        r = _StubResolver(impls={"fake": impl})
        result = dispatch_probe(
            LifecycleProbe(service="fake", method="probe_running"),
            resolver=r, now=0.0,
        )
        assert result.is_ok
        assert result.detail == "up"

    def test_failed_when_lifecycle_missing(self) -> None:
        r = _StubResolver(impls={})
        result = dispatch_probe(
            LifecycleProbe(service="not-there", method="probe_running"),
            resolver=r, now=0.0,
        )
        assert result.status == "failed"
        assert "no lifecycle" in result.detail

    def test_failed_when_method_missing(self) -> None:
        impl = _FakeLifecycle()
        r = _StubResolver(impls={"fake": impl})
        result = dispatch_probe(
            LifecycleProbe(service="fake", method="probe_nonexistent"),
            resolver=r, now=0.0,
        )
        assert result.status == "failed"
        assert "no method" in result.detail

    def test_unknown_when_lifecycle_method_raises(self) -> None:
        # Probes never raise — the dispatcher MUST convert exceptions
        # into ProbeResult.unknown so the orchestrator's invariant
        # ("every probe returns a ProbeResult") holds.
        class Boom:
            service_id = "boom"
            def probe_running(self, ctx): raise RuntimeError("kaboom")
            def probe_has_api_key(self, ctx): return ProbeResult.ok()
            def mint_api_key(self, ctx): return Outcome.success()
            def discover_api_key(self, ctx): return None
            def persist_api_key(self, k, ctx): return Outcome.success()
        r = _StubResolver(impls={"boom": Boom()})
        result = dispatch_probe(
            LifecycleProbe(service="boom", method="probe_running"),
            resolver=r, now=0.0,
        )
        assert result.status == "unknown"
        assert "kaboom" in result.detail


class TestLifecycleEnsurer:
    def test_returns_lifecycle_outcome(self) -> None:
        impl = _FakeLifecycle(mint_outcome=Outcome.success("minted"))
        r = _StubResolver(impls={"fake": impl})
        outcome = dispatch_ensurer(
            LifecycleEnsurer(service="fake", method="mint_api_key"),
            resolver=r, now=0.0,
        )
        assert outcome.ok
        assert outcome.value == "minted"

    def test_non_transient_when_lifecycle_missing(self) -> None:
        # Operator config error — orchestrator should NOT keep
        # retrying on cooldown.
        r = _StubResolver(impls={})
        outcome = dispatch_ensurer(
            LifecycleEnsurer(service="absent", method="mint_api_key"),
            resolver=r, now=0.0,
        )
        assert not outcome.ok
        assert outcome.transient is False

    def test_transient_when_lifecycle_method_raises(self) -> None:
        class Boom:
            service_id = "boom"
            def probe_running(self, ctx): return ProbeResult.ok()
            def probe_has_api_key(self, ctx): return ProbeResult.ok()
            def mint_api_key(self, ctx): raise RuntimeError("connection refused")
            def discover_api_key(self, ctx): return None
            def persist_api_key(self, k, ctx): return Outcome.success()
        r = _StubResolver(impls={"boom": Boom()})
        outcome = dispatch_ensurer(
            LifecycleEnsurer(service="boom", method="mint_api_key"),
            resolver=r, now=0.0,
        )
        assert not outcome.ok
        assert outcome.transient is True
        assert "connection refused" in outcome.error


# --- HTTP probe dispatch ----------------------------------------------


class TestHttpJsonProbe:
    @patch("urllib.request.urlopen")
    def test_ok_when_assert_passes(self, mock_open: MagicMock) -> None:
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'[{"id": 1}]'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp

        r = _StubResolver(configs={"bazarr": {"host": "bazarr", "port": 6767}})
        result = dispatch_probe(
            HttpJsonProbe(
                service="bazarr",
                path="/api/system/languages/profiles",
                assert_expr="len(response) > 0",
            ),
            resolver=r, now=0.0,
        )
        assert result.is_ok

    @patch("urllib.request.urlopen")
    def test_failed_when_assert_fails(self, mock_open: MagicMock) -> None:
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'[]'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp

        r = _StubResolver(configs={"bazarr": {"host": "bazarr", "port": 6767}})
        result = dispatch_probe(
            HttpJsonProbe(
                service="bazarr", path="/x", assert_expr="len(response) > 0",
            ),
            resolver=r, now=0.0,
        )
        assert result.status == "failed"

    @patch("urllib.request.urlopen")
    def test_unknown_on_network_error(self, mock_open: MagicMock) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")
        r = _StubResolver(configs={"x": {"host": "x", "port": 1}})
        result = dispatch_probe(
            HttpJsonProbe(service="x", path="/", assert_expr="True"),
            resolver=r, now=0.0,
        )
        assert result.status == "unknown"

    def test_failed_when_service_config_missing(self) -> None:
        r = _StubResolver(configs={})
        result = dispatch_probe(
            HttpJsonProbe(service="ghost", path="/", assert_expr="True"),
            resolver=r, now=0.0,
        )
        assert result.status == "failed"
        assert "url" in result.detail.lower()


# --- File probe dispatch ----------------------------------------------


class TestFileJsonProbe:
    def test_skip_if_missing(self, tmp_path: Path) -> None:
        # File absent + skip_if_missing=True → ok with skipped=True
        # in evidence. Lets promises gracefully no-op when the
        # underlying file is genuinely optional.
        result = dispatch_probe(
            FileJsonProbe(
                path=str(tmp_path / "absent.json"),
                assert_expr="True",
                skip_if_missing=True,
            ),
            resolver=_StubResolver(), now=0.0,
        )
        assert result.is_ok
        assert result.evidence.get("skipped") is True

    def test_failed_when_file_missing_without_skip(self, tmp_path: Path) -> None:
        result = dispatch_probe(
            FileJsonProbe(path=str(tmp_path / "absent.json"), assert_expr="True"),
            resolver=_StubResolver(), now=0.0,
        )
        assert result.status == "failed"

    def test_assert_against_data(self, tmp_path: Path) -> None:
        path = tmp_path / "x.json"
        path.write_text('{"main": {"apiKey": "k"}}')
        result = dispatch_probe(
            FileJsonProbe(
                path=str(path),
                assert_expr="data['main']['apiKey'] == 'k'",
            ),
            resolver=_StubResolver(), now=0.0,
        )
        assert result.is_ok


# --- File text probe --------------------------------------------------


def test_file_text_probe(tmp_path: Path) -> None:
    path = tmp_path / "config.txt"
    path.write_text("apikey = real-key")
    result = dispatch_probe(
        FileTextProbe(path=str(path), assert_expr="'real-key' in data"),
        resolver=_StubResolver(), now=0.0,
    )
    assert result.is_ok


# --- K8s probes (Phase 5+ stubs) --------------------------------------


def test_k8s_probes_return_unknown_with_phase5_message() -> None:
    # Until the kubectl-shellout integration lands, the orchestrator
    # records these as unknown. The legacy probe_promises CLI still
    # handles them; the orchestrator just doesn't re-implement.
    r = dispatch_probe(
        K8sResourceProbe(resource_kind="pvc", namespace="x", assert_expr="True"),
        resolver=_StubResolver(), now=0.0,
    )
    assert r.status == "unknown"
    assert "Phase 5" in r.detail

    r2 = dispatch_probe(
        K8sExecProbe(namespace="x", pod_label="app=y", assert_expr="True"),
        resolver=_StubResolver(), now=0.0,
    )
    assert r2.status == "unknown"


# --- Job ensurer ------------------------------------------------------


class TestJobEnsurer:
    def test_returns_failure_for_empty_job_name(self) -> None:
        outcome = dispatch_ensurer(
            JobEnsurer(job_name=""),
            resolver=_StubResolver(), now=0.0,
        )
        assert not outcome.ok
        assert outcome.transient is False

    def test_invokes_run_job_and_maps_ok(self) -> None:
        with patch(
            "media_stack.application.jobs.framework.run_job",
            return_value={"status": "ok", "elapsed": 0.5, "ok": 1},
        ) as mock_run:
            outcome = dispatch_ensurer(
                JobEnsurer(job_name="ensure-bazarr-language-profile"),
                resolver=_StubResolver(), now=0.0,
            )
        assert outcome.ok
        mock_run.assert_called_once_with(
            "ensure-bazarr-language-profile", source="orchestrator_shadow",
        )

    def test_transient_when_run_job_raises(self) -> None:
        with patch(
            "media_stack.application.jobs.framework.run_job",
            side_effect=RuntimeError("bus broken"),
        ):
            outcome = dispatch_ensurer(
                JobEnsurer(job_name="ensure-x"),
                resolver=_StubResolver(), now=0.0,
            )
        assert not outcome.ok
        assert outcome.transient is True

    def test_skipped_treated_as_success(self) -> None:
        # Phase-0 ensurers like jellyfin:ensure-api-key return
        # ``skipped: already_minted`` when the invariant already
        # holds. That's idempotent success — the re-probe will
        # confirm.
        with patch(
            "media_stack.application.jobs.framework.run_job",
            return_value={"skipped": "already_minted"},
        ):
            outcome = dispatch_ensurer(
                JobEnsurer(job_name="jellyfin:ensure-api-key"),
                resolver=_StubResolver(), now=0.0,
            )
        assert outcome.ok


# --- Deploy + Infra ensurers (no-op success) --------------------------


def test_deploy_ensurer_returns_externally_ensured() -> None:
    outcome = dispatch_ensurer(
        DeployEnsurer(target="jellyfin"),
        resolver=_StubResolver(), now=0.0,
    )
    assert outcome.ok
    assert outcome.evidence["reason"] == "externally_ensured"
    assert outcome.evidence["target"] == "jellyfin"


def test_infra_ensurer_returns_externally_ensured() -> None:
    outcome = dispatch_ensurer(
        InfraEnsurer(operator="kubectl-apply"),
        resolver=_StubResolver(), now=0.0,
    )
    assert outcome.ok
    assert outcome.evidence["operator"] == "kubectl-apply"
