"""Tests for the ADR-0006 Phase 1 spec protocols.

Pin the structural contract every probe / ensurer variant satisfies:

  * ``isinstance(value, ProbeSpecProtocol)`` returns True for each
    of the 8 ``ProbeSpec`` variants. Type-checkers use the Protocol
    to enforce the same shape on a future variant; the runtime
    check is for diagnostic code.
  * ``isinstance(value, EnsurerSpecProtocol)`` returns True for each
    of the 4 ``EnsurerSpec`` variants.
  * ``to_dict()`` round-trips: a parsed spec produces a YAML-shaped
    dict; feeding that dict back through the parser yields an equal
    spec. This is the symmetry guarantee the loader's diagnostic
    code (operator CLIs, log messages) relies on.
"""

from __future__ import annotations

import pytest

from media_stack.domain.services.promises import (
    DeployEnsurer,
    EnsurerSpecProtocol,
    FileJsonProbe,
    FileTextProbe,
    HttpJsonProbe,
    HttpStatusProbe,
    HttpTextProbe,
    InfraEnsurer,
    JobEnsurer,
    K8sExecProbe,
    K8sResourceProbe,
    LifecycleEnsurer,
    LifecycleProbe,
    ProbeSpecProtocol,
)
from media_stack.infrastructure.promises.registry import (
    EnsurerSpecParser,
    ProbeSpecParser,
)


# ----------------------------------------------------------------------
# Sample instances — one per variant, with non-default fields so the
# round-trip catches any fields the to_dict() shape forgets.
# ----------------------------------------------------------------------


_PROBE_SAMPLES: list = [
    LifecycleProbe(service="jellyfin", method="probe_running"),
    HttpJsonProbe(
        service="sonarr", path="/api/v3/system/status",
        auth="api_key", assert_expr="response is not None",
    ),
    HttpTextProbe(
        service="prowlarr", path="/healthz",
        auth="none", assert_expr="ok in body",
    ),
    HttpStatusProbe(
        service="gateway", path="/healthz",
        auth="none", assert_expr="status == 200",
    ),
    FileJsonProbe(
        path="jellyseerr/settings.json",
        assert_expr="data.get('radarr')",
        skip_if_missing=True,
    ),
    FileTextProbe(
        path="bazarr/config.ini",
        assert_expr="'language' in data",
        skip_if_missing=False,
    ),
    K8sResourceProbe(
        resource_kind="Service",
        namespace="media-stack",
        label_selector="app=envoy",
        assert_expr="len(items) >= 1",
    ),
    K8sExecProbe(
        namespace="media-stack",
        pod_label="app=jellyfin",
        container="jellyfin",
        command=("ls", "/config"),
        assert_expr="'jellyfin.db' in stdout",
        skip_if_unset="K8S_NAMESPACE",
    ),
]


_ENSURER_SAMPLES: list = [
    LifecycleEnsurer(service="jellyfin", method="mint_api_key"),
    JobEnsurer(job_name="ensure-jellyfin-libraries"),
    DeployEnsurer(target="jellyfin"),
    InfraEnsurer(operator="kubectl-apply"),
]


# ======================================================================
# Protocol conformance
# ======================================================================


class TestProbeSpecProtocolConformance:
    @pytest.mark.parametrize(
        "spec",
        _PROBE_SAMPLES,
        ids=lambda s: type(s).__name__,
    )
    def test_isinstance_check_passes(self, spec) -> None:
        assert isinstance(spec, ProbeSpecProtocol), (
            f"{type(spec).__name__} doesn't satisfy ProbeSpecProtocol "
            f"— missing kind: str or to_dict() -> dict"
        )

    @pytest.mark.parametrize(
        "spec",
        _PROBE_SAMPLES,
        ids=lambda s: type(s).__name__,
    )
    def test_kind_is_a_string(self, spec) -> None:
        assert isinstance(spec.kind, str)
        assert spec.kind  # non-empty discriminator

    def test_unrelated_object_with_kind_attr_does_not_satisfy(
        self,
    ) -> None:
        # ``runtime_checkable`` only checks attribute presence — but
        # we still want to make sure a random object missing
        # ``to_dict`` falls out.
        class _Bare:
            kind = "http_json"

        assert not isinstance(_Bare(), ProbeSpecProtocol)


class TestEnsurerSpecProtocolConformance:
    @pytest.mark.parametrize(
        "spec",
        _ENSURER_SAMPLES,
        ids=lambda s: type(s).__name__,
    )
    def test_isinstance_check_passes(self, spec) -> None:
        assert isinstance(spec, EnsurerSpecProtocol), (
            f"{type(spec).__name__} doesn't satisfy "
            f"EnsurerSpecProtocol"
        )

    @pytest.mark.parametrize(
        "spec",
        _ENSURER_SAMPLES,
        ids=lambda s: type(s).__name__,
    )
    def test_kind_is_a_string(self, spec) -> None:
        assert isinstance(spec.kind, str)
        assert spec.kind


# ======================================================================
# to_dict() round-trip via parsers
# ======================================================================


class TestProbeRoundTrip:
    @pytest.mark.parametrize(
        "spec",
        _PROBE_SAMPLES,
        ids=lambda s: type(s).__name__,
    )
    def test_to_dict_round_trips_via_parser(self, spec) -> None:
        # to_dict() produces the YAML shape; parser re-builds the
        # in-process dataclass. Round-trip must be loss-free.
        parser = ProbeSpecParser()
        rebuilt = parser.parse("p", spec.to_dict())
        assert rebuilt == spec, (
            f"round-trip drift for {type(spec).__name__}:\n"
            f"  original: {spec}\n"
            f"  rebuilt:  {rebuilt}\n"
            f"  shape:    {spec.to_dict()}"
        )

    @pytest.mark.parametrize(
        "spec",
        _PROBE_SAMPLES,
        ids=lambda s: type(s).__name__,
    )
    def test_to_dict_uses_yaml_field_names(self, spec) -> None:
        # The YAML schema renames ``kind`` (in-process discriminator)
        # to ``type``, and ``assert_expr`` to ``assert``. Pin both
        # so the loader's input shape stays canonical.
        d = spec.to_dict()
        assert "kind" not in d or type(spec) is K8sResourceProbe, (
            "to_dict() leaked the in-process ``kind`` field instead "
            "of renaming to ``type``"
        )
        assert "type" in d, (
            f"{type(spec).__name__}.to_dict() missing the ``type`` "
            f"discriminator: {d}"
        )
        assert "assert_expr" not in d, (
            "to_dict() leaked ``assert_expr`` instead of renaming "
            "to ``assert``"
        )


class TestEnsurerRoundTrip:
    @pytest.mark.parametrize(
        "spec",
        _ENSURER_SAMPLES,
        ids=lambda s: type(s).__name__,
    )
    def test_to_dict_round_trips_via_parser(self, spec) -> None:
        parser = EnsurerSpecParser()
        rebuilt = parser.parse("p", spec.to_dict())
        assert rebuilt == spec, (
            f"round-trip drift for {type(spec).__name__}:\n"
            f"  original: {spec}\n"
            f"  rebuilt:  {rebuilt}\n"
            f"  shape:    {spec.to_dict()}"
        )


# ======================================================================
# Edge cases — preserve the field-rename collisions on K8sResourceProbe
# and the tuple-to-list shape on K8sExecProbe
# ======================================================================


class TestK8sResourceProbeKindCollision:
    """``K8sResourceProbe`` has both a discriminator ``kind: Literal["k8s_resource"]``
    AND a payload field ``resource_kind`` that the YAML schema spells
    as ``kind``. ``to_dict()`` resolves the collision: the
    discriminator becomes ``type``, and ``resource_kind`` reclaims
    the ``kind`` slot. This double-rename has to round-trip exactly."""

    def test_resource_kind_appears_as_kind_in_dict(self) -> None:
        spec = K8sResourceProbe(
            resource_kind="Deployment",
            namespace="default",
            label_selector="app=foo",
            assert_expr="True",
        )
        d = spec.to_dict()
        assert d["type"] == "k8s_resource"  # discriminator
        assert d["kind"] == "Deployment"     # payload
        assert "resource_kind" not in d


class TestK8sExecProbeCommandShape:
    """``K8sExecProbe.command`` is a tuple in-process (frozen
    dataclass requirement) but a list in YAML / JSON. ``to_dict()``
    converts so the round-trip via the parser hits the
    ``isinstance(cmd, (list, tuple))`` guard cleanly."""

    def test_command_serialises_as_list(self) -> None:
        spec = K8sExecProbe(
            namespace="ns", pod_label="app=jf", container="jf",
            command=("ls", "-la", "/config"),
        )
        d = spec.to_dict()
        assert isinstance(d["command"], list)
        assert d["command"] == ["ls", "-la", "/config"]
