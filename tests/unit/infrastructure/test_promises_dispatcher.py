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


# --- K8s resource probes ----------------------------------------------


class _FakeK8sItem:
    """Mimics a kubernetes client list-response item — supports the
    ``.to_dict()`` accessor the dispatcher uses + the
    ``.metadata.name`` accessor used by the pod-exec probe."""

    def __init__(self, payload: dict, name: str = "") -> None:
        self._payload = payload
        meta = MagicMock()
        meta.name = name or payload.get("metadata", {}).get("name", "")
        self.metadata = meta

    def to_dict(self) -> dict:
        return self._payload


class _FakeK8sListResp:
    def __init__(self, items: list) -> None:
        self.items = items


def _patch_k8s_clients(*, core=None, apps=None, net=None):
    """Patch the dispatcher's k8s loader to return given API stubs.

    Each arg is a MagicMock (or None to use a fresh MagicMock). The
    ``_load_k8s_clients`` helper is patched to return the triple."""
    core = core or MagicMock()
    apps = apps or MagicMock()
    net = net or MagicMock()
    return patch(
        "media_stack.infrastructure.promises.dispatcher._load_k8s_clients",
        return_value=(core, apps, net),
    )


class TestK8sResourceProbe:
    def test_pvc_list_namespaced_passes_when_assert_holds(self) -> None:
        items = [
            _FakeK8sItem({"status": {"phase": "Bound"}, "metadata": {"name": "pvc-1"}}),
            _FakeK8sItem({"status": {"phase": "Bound"}, "metadata": {"name": "pvc-2"}}),
        ]
        core = MagicMock()
        core.list_namespaced_persistent_volume_claim.return_value = (
            _FakeK8sListResp(items)
        )
        with _patch_k8s_clients(core=core):
            result = dispatch_probe(
                K8sResourceProbe(
                    resource_kind="pvc",
                    namespace="media-stack",
                    assert_expr=(
                        "len(resources) > 0 and "
                        "all(p['status']['phase'] == 'Bound' for p in resources)"
                    ),
                ),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.is_ok, result.detail
        core.list_namespaced_persistent_volume_claim.assert_called_once_with(
            namespace="media-stack",
        )

    def test_pvc_assert_failure_is_failed_not_unknown(self) -> None:
        # One PVC in Pending — assert wants all Bound. The dispatcher
        # MUST report this as failed, not unknown (the API call worked,
        # the world just doesn't satisfy the invariant).
        items = [
            _FakeK8sItem({"status": {"phase": "Bound"}}),
            _FakeK8sItem({"status": {"phase": "Pending"}}),
        ]
        core = MagicMock()
        core.list_namespaced_persistent_volume_claim.return_value = (
            _FakeK8sListResp(items)
        )
        with _patch_k8s_clients(core=core):
            result = dispatch_probe(
                K8sResourceProbe(
                    resource_kind="pvc", namespace="x",
                    assert_expr="all(p['status']['phase'] == 'Bound' for p in resources)",
                ),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.status == "failed"

    def test_pv_is_cluster_scoped_ignores_namespace(self) -> None:
        # PV is cluster-scoped — the dispatcher must call
        # list_persistent_volume, NOT a namespaced variant, even
        # if the YAML happens to set a namespace.
        items = [
            _FakeK8sItem({
                "spec": {
                    "claimRef": {"name": "media-stack-media"},
                    "persistentVolumeReclaimPolicy": "Retain",
                },
            }),
        ]
        core = MagicMock()
        core.list_persistent_volume.return_value = _FakeK8sListResp(items)
        with _patch_k8s_clients(core=core):
            result = dispatch_probe(
                K8sResourceProbe(
                    resource_kind="pv",
                    namespace="ignored-because-cluster-scoped",
                    assert_expr=(
                        "any('media-stack-media' in p['spec']['claimRef']['name'] "
                        "and p['spec']['persistentVolumeReclaimPolicy'] == 'Retain' "
                        "for p in resources)"
                    ),
                ),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.is_ok
        core.list_persistent_volume.assert_called_once_with()
        core.list_namespaced_persistent_volume_claim.assert_not_called()

    def test_label_selector_passed_through(self) -> None:
        items = [_FakeK8sItem({"status": {"phase": "Running",
                                         "containerStatuses": [{"ready": True}]}})]
        core = MagicMock()
        core.list_namespaced_pod.return_value = _FakeK8sListResp(items)
        with _patch_k8s_clients(core=core):
            dispatch_probe(
                K8sResourceProbe(
                    resource_kind="pod", namespace="media-stack",
                    label_selector="app=authelia",
                    assert_expr="any(c.get('ready') for p in resources for c in p['status']['containerStatuses'])",
                ),
                resolver=_StubResolver(), now=0.0,
            )
        kwargs = core.list_namespaced_pod.call_args.kwargs
        assert kwargs["label_selector"] == "app=authelia"
        assert kwargs["namespace"] == "media-stack"

    def test_unsupported_resource_kind_is_failed(self) -> None:
        with _patch_k8s_clients():
            result = dispatch_probe(
                K8sResourceProbe(
                    resource_kind="customresource", namespace="x",
                    assert_expr="True",
                ),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.status == "failed"
        assert "unsupported kind" in result.detail

    def test_empty_resource_kind_is_failed(self) -> None:
        result = dispatch_probe(
            K8sResourceProbe(resource_kind="", assert_expr="True"),
            resolver=_StubResolver(), now=0.0,
        )
        assert result.status == "failed"
        assert "missing 'kind'" in result.detail

    def test_k8s_unavailable_returns_unknown(self) -> None:
        # When the dispatcher can't load the k8s client (running on
        # compose, kubeconfig absent, etc.) it returns unknown so the
        # orchestrator's cooldown applies — not failed (which would
        # imply we KNOW the world doesn't satisfy the promise).
        with patch(
            "media_stack.infrastructure.promises.dispatcher._load_k8s_clients",
            return_value=None,
        ):
            result = dispatch_probe(
                K8sResourceProbe(resource_kind="pvc", namespace="x",
                                 assert_expr="True"),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.status == "unknown"
        assert "k8s client unavailable" in result.detail

    def test_api_exception_becomes_unknown(self) -> None:
        # ApiException / RBAC failure / network blip → unknown,
        # NOT failed. The cooldown tracker re-tries on transient
        # backoff; a "failed" status would imply the cluster is
        # answering with "no, it's not bound" which is false here.
        core = MagicMock()
        core.list_namespaced_pod.side_effect = RuntimeError("403 Forbidden")
        with _patch_k8s_clients(core=core):
            result = dispatch_probe(
                K8sResourceProbe(resource_kind="pod", namespace="x",
                                 assert_expr="True"),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.status == "unknown"
        assert "list failed" in result.detail


# --- K8s pod-command probes -------------------------------------------


class TestK8sPodCommandProbe:
    def test_running_pod_command_passes_when_stdout_satisfies_assert(self) -> None:
        pods = _FakeK8sListResp([_FakeK8sItem({}, name="envoy-abc123")])
        core = MagicMock()
        core.list_namespaced_pod.return_value = pods
        with _patch_k8s_clients(core=core), \
                patch("kubernetes.stream.stream", return_value="my.host.example.com\n") as patched_stream:
            result = dispatch_probe(
                K8sExecProbe(
                    namespace="media-stack", pod_label="app=envoy",
                    container="envoy",
                    command=("sh", "-c", "grep my.host /etc/envoy/envoy.yaml"),
                    assert_expr="len(data.strip()) > 0",
                ),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.is_ok
        # Pod lookup uses field selector for status.phase=Running so
        # only ready-to-run pods get picked.
        core.list_namespaced_pod.assert_called_once()
        kw = core.list_namespaced_pod.call_args.kwargs
        assert kw["field_selector"] == "status.phase=Running"
        assert kw["label_selector"] == "app=envoy"
        # Stream call carried the container kwarg (we explicitly chose envoy).
        stream_kwargs = patched_stream.call_args.kwargs
        assert stream_kwargs["container"] == "envoy"

    def test_no_running_pod_is_failed(self) -> None:
        core = MagicMock()
        core.list_namespaced_pod.return_value = _FakeK8sListResp([])
        with _patch_k8s_clients(core=core):
            result = dispatch_probe(
                K8sExecProbe(
                    namespace="media-stack", pod_label="app=missing",
                    command=("echo", "x"), assert_expr="data == 'x\\n'",
                ),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.status == "failed"
        assert "no Running pod" in result.detail

    def test_skip_if_unset_passes_with_skipped_message(self) -> None:
        # The probe references gateway_host but the deployment hasn't
        # configured it — promise is N/A; pass with a "skipped" detail
        # so it doesn't poison acceptance.
        with patch(
            "media_stack.infrastructure.promises.dispatcher."
            "_resolve_routing_vars_for_substitution",
            return_value={},
        ):
            result = dispatch_probe(
                K8sExecProbe(
                    namespace="media-stack", pod_label="app=envoy",
                    command=("sh", "-c", "grep ${gateway_host} /etc/envoy/envoy.yaml"),
                    assert_expr="len(data) > 0",
                    skip_if_unset="gateway_host",
                ),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.is_ok
        assert "skipped" in result.detail

    def test_routing_var_substitution_in_command_and_assert(self) -> None:
        # Assert references ${gateway_host}; both the command's grep
        # arg and the assert literal must get the substituted value.
        pods = _FakeK8sListResp([_FakeK8sItem({}, name="hp-pod")])
        core = MagicMock()
        core.list_namespaced_pod.return_value = pods

        captured_cmd: list = []

        def _fake_stream(_func, _pod, _ns, **kw):
            captured_cmd.extend(kw["command"])
            return "0\n"

        with _patch_k8s_clients(core=core), \
                patch(
                    "media_stack.infrastructure.promises.dispatcher."
                    "_resolve_routing_vars_for_substitution",
                    return_value={"gateway_host": "real.example.com"},
                ), patch("kubernetes.stream.stream", side_effect=_fake_stream):
            result = dispatch_probe(
                K8sExecProbe(
                    namespace="media-stack", pod_label="app=homepage",
                    command=(
                        "sh", "-c",
                        "grep -F 'apps.media-stack.local' /app/cfg | wc -l",
                    ),
                    assert_expr=(
                        "data.strip() == '0' or "
                        "'${gateway_host}' == 'apps.media-stack.local'"
                    ),
                    skip_if_unset="gateway_host",
                ),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.is_ok, result.detail
        # The command in this promise has no ${gateway_host} placeholder,
        # so the literal apps.media-stack.local stays unchanged. The
        # assert IS substituted: 'real.example.com' != 'apps.media-stack.local'
        # leaves data.strip() == '0' as the satisfying branch.
        assert "apps.media-stack.local" in " ".join(captured_cmd)

    def test_missing_namespace_is_failed(self) -> None:
        result = dispatch_probe(
            K8sExecProbe(namespace="", pod_label="app=x",
                         command=("echo",), assert_expr="True"),
            resolver=_StubResolver(), now=0.0,
        )
        assert result.status == "failed"
        assert "missing namespace" in result.detail

    def test_missing_command_is_failed(self) -> None:
        result = dispatch_probe(
            K8sExecProbe(namespace="x", pod_label="app=x",
                         command=(), assert_expr="True"),
            resolver=_StubResolver(), now=0.0,
        )
        assert result.status == "failed"

    def test_k8s_unavailable_returns_unknown(self) -> None:
        with patch(
            "media_stack.infrastructure.promises.dispatcher._load_k8s_clients",
            return_value=None,
        ):
            result = dispatch_probe(
                K8sExecProbe(namespace="x", pod_label="app=x",
                             command=("echo",), assert_expr="True"),
                resolver=_StubResolver(), now=0.0,
            )
        assert result.status == "unknown"
        assert "k8s client unavailable" in result.detail


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


# --- Synthetic service-id resolution (Phase 4d) ----------------------


class TestSyntheticServiceUrls:
    """Promises like ``adaptive-search-scheduled`` (service:
    ``controller``) and ``gateway-https-listener-up`` (service:
    ``gateway_https``) reference services that don't have a
    contracts/services YAML — those names are synthetic. The legacy
    probe_promises CLI hardcodes their URLs; the orchestrator must
    too, otherwise these promises always fail with "can't build url"
    even though the legacy CLI handles them fine."""

    @patch("urllib.request.urlopen")
    def test_controller_resolves_to_localhost_9100(
        self, mock_open: MagicMock,
    ) -> None:
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"ok": true}'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp

        # Resolver returns no service config for "controller" —
        # synthetic resolution must fire.
        r = _StubResolver(configs={})
        result = dispatch_probe(
            HttpJsonProbe(
                service="controller", path="/api/jobs", auth="none",
                assert_expr="response['ok']",
            ),
            resolver=r, now=0.0,
        )
        assert result.is_ok
        # Caller hit localhost:9100, not "" (which produced the bug).
        called_url = mock_open.call_args[0][0]
        called_url = called_url.full_url if hasattr(called_url, "full_url") else called_url
        assert "localhost:9100" in str(called_url)

    @patch("urllib.request.urlopen")
    def test_gateway_https_resolves_compose_to_envoy_8880(
        self, mock_open: MagicMock, monkeypatch,
    ) -> None:
        # KUBERNETES_SERVICE_HOST absent → compose layout. The
        # orchestrator runs INSIDE the controller container, so the
        # public host:443 mapping isn't reachable here — must hit
        # envoy's internal TLS listener (8880) directly.
        monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{}'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp

        r = _StubResolver(configs={})
        result = dispatch_probe(
            HttpJsonProbe(
                service="gateway_https", path="/health", auth="none",
                assert_expr="True",
            ),
            resolver=r, now=0.0,
        )
        assert result.is_ok
        called_url = mock_open.call_args[0][0]
        called_url = called_url.full_url if hasattr(called_url, "full_url") else called_url
        assert "https://envoy:8880/health" in str(called_url), (
            f"expected internal envoy:8880, got {called_url!r}"
        )
        # TLS verification disabled for envoy:8880 (self-signed cert)
        ssl_ctx = mock_open.call_args.kwargs.get("context")
        assert ssl_ctx is not None and ssl_ctx.verify_mode.name == "CERT_NONE"

    @patch("urllib.request.urlopen")
    def test_gateway_https_resolves_k8s_to_envoy(
        self, mock_open: MagicMock, monkeypatch,
    ) -> None:
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{}'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp

        r = _StubResolver(configs={})
        dispatch_probe(
            HttpJsonProbe(
                service="gateway_https", path="/", auth="none",
                assert_expr="True",
            ),
            resolver=r, now=0.0,
        )
        called_url = mock_open.call_args[0][0]
        called_url = called_url.full_url if hasattr(called_url, "full_url") else called_url
        assert "envoy:80" in str(called_url)

    def test_unknown_synthetic_service_fails_clean(self) -> None:
        # A service id that's neither in contracts/services NOR in
        # the synthetic list should produce a clean "can't build url"
        # failure, not a crash.
        r = _StubResolver(configs={})
        result = dispatch_probe(
            HttpJsonProbe(
                service="not-a-real-service", path="/", auth="none",
                assert_expr="True",
            ),
            resolver=r, now=0.0,
        )
        assert result.status == "failed"
        assert "url" in result.detail.lower()


# --- jellyfin_key auth alias (Phase 4d) ------------------------------


class TestControllerBasicAuth:
    """``auth: controller_basic`` is HTTP Basic against the
    controller's own API as the seeded stack admin. Used by promises
    that probe controller-served endpoints (``/api/jobs``,
    ``/api/auth/config``, etc.). Without this header, every probe
    lands on 401 and reports failed_transient — same bug class as
    the unhandled ``jellyfin_key`` was for jellyfin-libraries."""

    @patch("urllib.request.urlopen")
    def test_basic_auth_header_uses_stack_admin_creds(
        self, mock_open: MagicMock, monkeypatch,
    ) -> None:
        monkeypatch.setenv("STACK_ADMIN_USERNAME", "admin")
        monkeypatch.setenv("STACK_ADMIN_PASSWORD", "rotate-me-please")
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"jobs": []}'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp

        r = _StubResolver(configs={})
        dispatch_probe(
            HttpJsonProbe(
                service="controller",
                path="/api/jobs",
                auth="controller_basic",
                assert_expr="True",
            ),
            resolver=r, now=0.0,
        )
        req = mock_open.call_args[0][0]
        headers = {k.lower(): v for k, v in req.headers.items()}
        assert headers.get("authorization", "").startswith("Basic "), (
            f"expected Basic auth header, got {headers}"
        )
        # Round-trip the base64 to confirm the seeded creds reached
        # the request.
        import base64
        token = headers["authorization"].split(" ", 1)[1]
        decoded = base64.b64decode(token).decode()
        assert decoded == "admin:rotate-me-please"

    def test_returns_empty_when_password_unset(self, monkeypatch) -> None:
        # Don't fabricate a Basic header with an empty password —
        # better to let the probe hit 401 honestly so the operator
        # sees "STACK_ADMIN_PASSWORD missing" rather than "wrong
        # creds".
        monkeypatch.delenv("STACK_ADMIN_PASSWORD", raising=False)
        from media_stack.infrastructure.promises.dispatcher import (
            _controller_basic_headers,
        )
        assert _controller_basic_headers(None) == {}


class TestJellyfinKeyAuth:
    """``auth: jellyfin_key`` is a promise-author shorthand for
    "use Jellyfin's API key with its custom X-Emby-Token header".
    The dispatcher resolves it identically to ``auth: api_key``
    against the jellyfin contract."""

    @patch("urllib.request.urlopen")
    def test_jellyfin_key_sets_x_emby_token_header(
        self, mock_open: MagicMock, monkeypatch,
    ) -> None:
        monkeypatch.setenv("JELLYFIN_API_KEY", "the-key")
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'[]'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp

        r = _StubResolver(configs={
            "jellyfin": {
                "host": "jellyfin", "port": 8096,
                "api_key_env": "JELLYFIN_API_KEY",
                "auth_mode": "X-Emby-Token",
            },
        })
        dispatch_probe(
            HttpJsonProbe(
                service="jellyfin",
                path="/Library/VirtualFolders",
                auth="jellyfin_key",
                assert_expr="isinstance(response, list)",
            ),
            resolver=r, now=0.0,
        )
        # The Request object's headers dict carries the auth.
        req = mock_open.call_args[0][0]
        # urllib normalizes header names to title case.
        headers = {k.lower(): v for k, v in req.headers.items()}
        assert headers.get("x-emby-token") == "the-key"
