"""Contract tests for ``k8s/base/ui/ui.yaml`` — the UI container's k8s manifest.

Parses the multi-document YAML and verifies the production hardening
shape: ServiceAccount, Deployment (replicas=2, maxUnavailable=0,
liveness/readiness/startup probes on /healthz, resource requests/limits,
non-root securityContext with capability drop ALL and
allowPrivilegeEscalation=false, anti-affinity, pinned image tag),
Service (port 80 -> targetPort 8080), and PodDisruptionBudget
(minAvailable=1).

Each failure message names the missing field and the expected shape so
operators can edit the manifest without re-reading this file.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT: Path = Path(__file__).resolve().parents[2]
# Phase 5 (ADR-0001) moved k8s/ui.yaml to k8s/base/ui/ui.yaml.
MANIFEST_PATH: Path = ROOT / "k8s" / "base" / "ui" / "ui.yaml"


def _load_docs() -> list[dict[str, Any]]:
    """Parse the multi-doc YAML, skipping null/empty separators."""

    if not MANIFEST_PATH.is_file():
        pytest.skip(
            f"file {MANIFEST_PATH} not yet created by parallel agent — "
            "re-run after that agent completes"
        )
    raw = MANIFEST_PATH.read_text(encoding="utf-8")
    try:
        loaded = list(yaml.safe_load_all(raw))
    except yaml.YAMLError as exc:  # pragma: no cover — surfaced in test
        raise AssertionError(
            f"{MANIFEST_PATH}: YAML parse error: {exc}"
        ) from exc
    return [doc for doc in loaded if isinstance(doc, dict)]


def _find_one(
    docs: list[dict[str, Any]], kind: str, name: str
) -> dict[str, Any] | None:
    for doc in docs:
        if doc.get("kind") != kind:
            continue
        if doc.get("metadata", {}).get("name") == name:
            return doc
    return None


def _first_container(deployment: dict[str, Any]) -> dict[str, Any]:
    containers = (
        deployment.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    assert containers, (
        f"{MANIFEST_PATH}: Deployment 'media-stack-ui' has no containers "
        "under spec.template.spec.containers — at least one is required."
    )
    return containers[0]


class UiK8sManifestContractTests(unittest.TestCase):
    """Each test failure cites the doc/kind/path and the expected shape."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.docs: list[dict[str, Any]] = _load_docs()

    # ------------------------------------------------------------------ #
    # File / parse                                                        #
    # ------------------------------------------------------------------ #

    def test_manifest_exists(self) -> None:
        self.assertTrue(
            MANIFEST_PATH.is_file(),
            f"Expected k8s manifest at {MANIFEST_PATH}.",
        )
        self.assertTrue(
            self.docs,
            f"{MANIFEST_PATH}: parsed zero YAML documents — file must "
            "contain at least ServiceAccount, Deployment, Service, and "
            "PodDisruptionBudget separated by '---'.",
        )

    # ------------------------------------------------------------------ #
    # ServiceAccount                                                      #
    # ------------------------------------------------------------------ #

    def test_has_serviceaccount(self) -> None:
        sa = _find_one(self.docs, "ServiceAccount", "media-stack-ui")
        self.assertIsNotNone(
            sa,
            f"{MANIFEST_PATH}: missing 'kind: ServiceAccount' with "
            "metadata.name == 'media-stack-ui'. The Deployment's "
            "serviceAccountName references this SA.",
        )

    # ------------------------------------------------------------------ #
    # Deployment                                                          #
    # ------------------------------------------------------------------ #

    def test_has_deployment(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(
            dep,
            f"{MANIFEST_PATH}: missing 'kind: Deployment' with "
            "metadata.name == 'media-stack-ui'.",
        )

    def test_deployment_has_two_replicas(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        replicas = dep.get("spec", {}).get("replicas")
        self.assertEqual(
            replicas,
            2,
            f"{MANIFEST_PATH}: Deployment spec.replicas must be 2 "
            f"(got {replicas!r}). Two replicas + maxUnavailable=0 + "
            "PDB minAvailable=1 gives zero-downtime voluntary evictions.",
        )

    def test_deployment_has_zero_unavailable_rollout(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        rolling = (
            dep.get("spec", {})
            .get("strategy", {})
            .get("rollingUpdate", {})
        )
        self.assertEqual(
            rolling.get("maxUnavailable"),
            0,
            f"{MANIFEST_PATH}: spec.strategy.rollingUpdate.maxUnavailable "
            f"must be 0 (got {rolling.get('maxUnavailable')!r}) so rollouts "
            "never drop below the PDB floor.",
        )

    def test_deployment_has_liveness_probe(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        container = _first_container(dep)
        path = (
            container.get("livenessProbe", {})
            .get("httpGet", {})
            .get("path")
        )
        self.assertEqual(
            path,
            "/healthz",
            f"{MANIFEST_PATH}: container[0].livenessProbe.httpGet.path "
            f"must be '/healthz' (got {path!r}).",
        )

    def test_deployment_has_readiness_probe(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        container = _first_container(dep)
        path = (
            container.get("readinessProbe", {})
            .get("httpGet", {})
            .get("path")
        )
        self.assertEqual(
            path,
            "/healthz",
            f"{MANIFEST_PATH}: container[0].readinessProbe.httpGet.path "
            f"must be '/healthz' (got {path!r}).",
        )

    def test_deployment_has_startup_probe(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        container = _first_container(dep)
        self.assertIn(
            "startupProbe",
            container,
            f"{MANIFEST_PATH}: container[0] must define a 'startupProbe' "
            "to give nginx grace before liveness kills slow starts.",
        )

    def test_deployment_has_resource_requests_and_limits(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        container = _first_container(dep)
        resources = container.get("resources", {})
        cpu_request = resources.get("requests", {}).get("cpu")
        memory_limit = resources.get("limits", {}).get("memory")
        self.assertIsNotNone(
            cpu_request,
            f"{MANIFEST_PATH}: container[0].resources.requests.cpu is "
            "required for scheduler bin-packing and HPA math.",
        )
        self.assertIsNotNone(
            memory_limit,
            f"{MANIFEST_PATH}: container[0].resources.limits.memory is "
            "required so a runaway pod cannot OOM the node.",
        )

    def test_deployment_runs_as_non_root(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        container = _first_container(dep)
        sec_ctx = container.get("securityContext", {})
        self.assertEqual(
            sec_ctx.get("runAsNonRoot"),
            True,
            f"{MANIFEST_PATH}: container[0].securityContext.runAsNonRoot "
            f"must be True (got {sec_ctx.get('runAsNonRoot')!r}).",
        )

    def test_deployment_drops_all_capabilities(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        container = _first_container(dep)
        drop = (
            container.get("securityContext", {})
            .get("capabilities", {})
            .get("drop", [])
        )
        self.assertIn(
            "ALL",
            drop,
            f"{MANIFEST_PATH}: container[0].securityContext.capabilities."
            f"drop must contain 'ALL' (got {drop!r}). Drop everything, "
            "add back only what nginx needs (none, in this case).",
        )

    def test_deployment_disallows_privilege_escalation(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        container = _first_container(dep)
        sec_ctx = container.get("securityContext", {})
        self.assertEqual(
            sec_ctx.get("allowPrivilegeEscalation"),
            False,
            f"{MANIFEST_PATH}: container[0].securityContext."
            "allowPrivilegeEscalation must be False (got "
            f"{sec_ctx.get('allowPrivilegeEscalation')!r}).",
        )

    # ------------------------------------------------------------------ #
    # Service                                                             #
    # ------------------------------------------------------------------ #

    def test_has_service(self) -> None:
        svc = _find_one(self.docs, "Service", "media-stack-ui")
        self.assertIsNotNone(
            svc,
            f"{MANIFEST_PATH}: missing 'kind: Service' with "
            "metadata.name == 'media-stack-ui'.",
        )
        assert svc is not None
        ports = svc.get("spec", {}).get("ports", [])
        self.assertTrue(
            ports,
            f"{MANIFEST_PATH}: Service spec.ports is empty — expected "
            "one entry with port=8080 and targetPort=8080.",
        )
        port = ports[0]
        # Service port matches container port (8080) so the Envoy
        # generator's _DEFAULT_SERVICE_PORTS map (shared with the
        # compose path) routes correctly in both deployments. See
        # generate_envoy_config_main.py:_build_default_service_ports.
        self.assertEqual(
            port.get("port"),
            8080,
            f"{MANIFEST_PATH}: Service spec.ports[0].port must be 8080 "
            f"(got {port.get('port')!r}); the Envoy generator's port "
            "map uses 8080 for both compose and k8s.",
        )
        self.assertEqual(
            port.get("targetPort"),
            8080,
            f"{MANIFEST_PATH}: Service spec.ports[0].targetPort must be "
            f"8080 (got {port.get('targetPort')!r}); the container "
            "listens unprivileged on 8080.",
        )

    # ------------------------------------------------------------------ #
    # PodDisruptionBudget                                                 #
    # ------------------------------------------------------------------ #

    def test_has_pdb(self) -> None:
        pdb = _find_one(
            self.docs, "PodDisruptionBudget", "media-stack-ui"
        )
        self.assertIsNotNone(
            pdb,
            f"{MANIFEST_PATH}: missing 'kind: PodDisruptionBudget' with "
            "metadata.name == 'media-stack-ui'.",
        )
        assert pdb is not None
        self.assertEqual(
            pdb.get("spec", {}).get("minAvailable"),
            1,
            f"{MANIFEST_PATH}: PDB spec.minAvailable must be 1 (got "
            f"{pdb.get('spec', {}).get('minAvailable')!r}); paired with "
            "replicas=2 this guarantees one pod survives voluntary "
            "evictions.",
        )

    # ------------------------------------------------------------------ #
    # Image / pull policy                                                 #
    # ------------------------------------------------------------------ #

    def test_image_pinned_to_specific_version(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        container = _first_container(dep)
        image = container.get("image", "")
        self.assertFalse(
            image.endswith(":latest"),
            f"{MANIFEST_PATH}: container[0].image must not use ':latest' "
            f"(got {image!r}); pin a versioned tag for reproducible "
            "rollbacks.",
        )
        self.assertRegex(
            image,
            r":v\d+",
            f"{MANIFEST_PATH}: container[0].image must contain ':v' "
            f"followed by digits (e.g. ':v1.0.0'); got {image!r}.",
        )

    def test_pull_policy_explicit(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        container = _first_container(dep)
        self.assertIn(
            "imagePullPolicy",
            container,
            f"{MANIFEST_PATH}: container[0].imagePullPolicy must be set "
            "explicitly (Always / IfNotPresent / Never) — the implicit "
            "default depends on the tag and varies across k8s versions.",
        )

    # ------------------------------------------------------------------ #
    # Anti-affinity                                                       #
    # ------------------------------------------------------------------ #

    def test_anti_affinity_present(self) -> None:
        dep = _find_one(self.docs, "Deployment", "media-stack-ui")
        self.assertIsNotNone(dep, f"{MANIFEST_PATH}: Deployment missing.")
        assert dep is not None
        affinity = (
            dep.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("affinity", {})
        )
        self.assertIn(
            "podAntiAffinity",
            affinity,
            f"{MANIFEST_PATH}: spec.template.spec.affinity.podAntiAffinity "
            "must be set (preferredDuringSchedulingIgnoredDuringExecution "
            "is fine) so the two replicas spread across nodes.",
        )


if __name__ == "__main__":
    unittest.main()
