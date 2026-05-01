"""Tests for the promise value types — ADR-0003 Phase 4a.

Pin the structural contract: discriminated unions for ProbeSpec /
EnsurerSpec, frozen dataclasses, ``Promise.applies_to`` semantics.
The loader + dispatcher are tested separately.
"""

from __future__ import annotations

import pytest

from media_stack.domain.services.promises import (
    DeployEnsurer,
    EnsurerSpec,
    FileJsonProbe,
    HttpJsonProbe,
    InfraEnsurer,
    JobEnsurer,
    K8sResourceProbe,
    LifecycleEnsurer,
    LifecycleProbe,
    Promise,
    PromiseRegistryError,
    ProbeSpec,
)


class TestProbeKinds:
    def test_each_probe_carries_a_distinct_kind(self) -> None:
        # The orchestrator dispatches on .kind. Drift here = wrong
        # dispatcher invoked silently.
        assert LifecycleProbe().kind == "lifecycle"
        assert HttpJsonProbe().kind == "http_json"
        assert FileJsonProbe().kind == "file_json"
        assert K8sResourceProbe().kind == "k8s_resource"

    def test_lifecycle_probe_carries_service_and_method(self) -> None:
        p = LifecycleProbe(service="jellyfin", method="probe_running")
        assert p.service == "jellyfin"
        assert p.method == "probe_running"

    def test_http_json_probe_carries_assert_expr(self) -> None:
        p = HttpJsonProbe(
            service="bazarr",
            path="/api/system/languages/profiles",
            auth="api_key",
            assert_expr="isinstance(response, list) and len(response) > 0",
        )
        assert "len(response)" in p.assert_expr
        assert p.auth == "api_key"

    def test_probes_are_frozen(self) -> None:
        p = LifecycleProbe(service="jellyfin", method="probe_running")
        with pytest.raises(Exception):
            p.service = "sonarr"  # type: ignore[misc]


class TestEnsurerKinds:
    def test_each_ensurer_kind_distinct(self) -> None:
        assert LifecycleEnsurer().kind == "lifecycle"
        assert JobEnsurer().kind == "job"
        assert DeployEnsurer().kind == "deploy"
        assert InfraEnsurer().kind == "infra"

    def test_job_ensurer_carries_job_name(self) -> None:
        e = JobEnsurer(job_name="ensure-bazarr-language-profile")
        assert e.job_name == "ensure-bazarr-language-profile"

    def test_lifecycle_ensurer_carries_service_and_method(self) -> None:
        e = LifecycleEnsurer(service="jellyfin", method="mint_api_key")
        assert e.service == "jellyfin"
        assert e.method == "mint_api_key"

    def test_infra_ensurer_carries_operator_token(self) -> None:
        # The orchestrator records these as externally ensured and
        # only re-probes — kubectl-apply is the operator/platform's
        # job, not the controller's.
        e = InfraEnsurer(operator="kubectl-apply")
        assert e.operator == "kubectl-apply"


class TestPromise:
    def test_constructs_with_typed_probe_and_ensurer(self) -> None:
        p = Promise(
            id="jellyfin-running",
            description="Jellyfin HTTP endpoint responds",
            platforms=("compose", "k8s"),
            probe=LifecycleProbe(service="jellyfin", method="probe_running"),
            ensurer=DeployEnsurer(target="jellyfin"),
        )
        assert p.id == "jellyfin-running"
        assert p.probe.kind == "lifecycle"
        assert p.ensurer.kind == "deploy"

    def test_applies_to_filters_by_platform(self) -> None:
        # The orchestrator skips compose-only promises on k8s and
        # vice versa. ``applies_to`` is the gate.
        p = Promise(
            id="x",
            description="",
            platforms=("compose",),
            probe=LifecycleProbe(),
            ensurer=JobEnsurer(),
        )
        assert p.applies_to("compose")
        assert not p.applies_to("k8s")

    def test_depends_on_default_empty(self) -> None:
        p = Promise(
            id="x",
            description="",
            platforms=("compose",),
            probe=LifecycleProbe(),
            ensurer=JobEnsurer(),
        )
        assert p.depends_on == ()

    def test_depends_on_round_trip(self) -> None:
        # Topological sort in the orchestrator (Phase 4b) walks
        # depends_on. Tuple-of-id is the contract.
        p = Promise(
            id="sonarr-jellyfin-notifier",
            description="",
            platforms=("compose", "k8s"),
            probe=LifecycleProbe(),
            ensurer=JobEnsurer(),
            depends_on=("jellyfin-api-key-discoverable", "sonarr-running"),
        )
        assert p.depends_on == (
            "jellyfin-api-key-discoverable", "sonarr-running",
        )

    def test_promise_is_frozen(self) -> None:
        p = Promise(
            id="x",
            description="",
            platforms=("compose",),
            probe=LifecycleProbe(),
            ensurer=JobEnsurer(),
        )
        with pytest.raises(Exception):
            p.id = "y"  # type: ignore[misc]


class TestErrors:
    def test_registry_error_is_value_error(self) -> None:
        # Subclassing ValueError lets callers catch (ValueError,) for
        # both YAML-shape errors and other validation issues.
        assert issubclass(PromiseRegistryError, ValueError)
