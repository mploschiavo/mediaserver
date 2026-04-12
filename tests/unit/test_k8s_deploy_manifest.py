"""Validate k8s-deploy.yaml manifest for deployment reliability.

These tests catch the class of bugs that cause CrashLoopBackOff or
ContainerCreating hangs on fresh K8s deploys:
  - ConfigMaps referenced but not defined in the manifest
  - Missing optional: true on ConfigMap volumes
  - Envoy port conflicts (port 80 in non-root containers)
  - Init container stale-config handling
  - Service targetPort mismatches

Run with: python -m pytest tests/unit/test_k8s_deploy_manifest.py -v
"""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "dist" / "k8s-deploy.yaml"


def _load_documents() -> list[dict]:
    """Load all YAML documents from the k8s-deploy manifest."""
    if not MANIFEST_PATH.is_file():
        return []
    docs = []
    for doc in yaml.safe_load_all(MANIFEST_PATH.read_text(encoding="utf-8")):
        if doc:
            docs.append(doc)
    return docs


def _find_by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == kind]


def _find_named(docs: list[dict], kind: str, name: str) -> dict | None:
    for d in docs:
        if d.get("kind") == kind and d.get("metadata", {}).get("name") == name:
            return d
    return None


DOCS = _load_documents()


class TestManifestLoads(unittest.TestCase):
    def test_manifest_file_exists(self):
        self.assertTrue(MANIFEST_PATH.is_file(), f"Missing {MANIFEST_PATH}")

    def test_has_multiple_documents(self):
        self.assertGreater(len(DOCS), 10)

    def test_has_namespace(self):
        ns = _find_by_kind(DOCS, "Namespace")
        self.assertTrue(ns, "No Namespace resource in manifest")


class TestConfigMapsExist(unittest.TestCase):
    """Every ConfigMap referenced by a volume MUST be defined in the manifest."""

    def _get_defined_configmaps(self) -> set[str]:
        return {
            d["metadata"]["name"]
            for d in _find_by_kind(DOCS, "ConfigMap")
            if d.get("metadata", {}).get("name")
        }

    def _get_referenced_configmaps(self) -> list[tuple[str, bool]]:
        """Find all configMap volume references with optional flag."""
        refs = []
        text = MANIFEST_PATH.read_text()
        for m in re.finditer(r"configMap:\s*\n\s*name:\s*(\S+)(?:\s*\n\s*optional:\s*(true|false))?", text):
            name = m.group(1)
            optional = m.group(2) == "true" if m.group(2) else False
            refs.append((name, optional))
        return refs

    def test_all_required_configmaps_are_defined(self):
        defined = self._get_defined_configmaps()
        referenced = self._get_referenced_configmaps()
        missing = {name for name, optional in referenced if not optional and name not in defined}
        self.assertFalse(
            missing,
            f"Required ConfigMaps referenced but not defined in manifest: {missing}. "
            "Pods will fail to schedule without these.",
        )


class TestConfigMapOptionalFlags(unittest.TestCase):
    """ConfigMap volumes that may not exist at first deploy should be optional."""

    def test_controller_config_is_optional_in_deployment(self):
        text = MANIFEST_PATH.read_text()
        # Find controller deployment's configMap reference
        # It should have optional: true
        pattern = r"name: media-stack-controller-config\s*\n\s*optional:\s*true"
        matches = re.findall(pattern, text)
        self.assertGreaterEqual(len(matches), 1,
            "media-stack-controller-config must have optional: true in controller Deployment")

    def test_cronjob_configmaps_are_optional(self):
        cronjobs = _find_by_kind(DOCS, "CronJob")
        for cj in cronjobs:
            cj_name = cj["metadata"]["name"]
            spec = cj.get("spec", {}).get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {})
            for vol in spec.get("volumes", []):
                cm = vol.get("configMap")
                if cm and cm.get("name", "").startswith("media-stack-controller"):
                    self.assertTrue(
                        cm.get("optional", False),
                        f"CronJob {cj_name}: configMap {cm['name']} must have optional: true",
                    )


class TestEnvoyPortConfig(unittest.TestCase):
    """Envoy must use non-privileged port 8880 in K8s (non-root)."""

    def test_envoy_base_template_uses_8880(self):
        cm = _find_named(DOCS, "ConfigMap", "media-stack-envoy-base-template")
        self.assertIsNotNone(cm, "envoy base template ConfigMap not found")
        template_yaml = cm.get("data", {}).get("envoy.runtime.base.yaml", "")
        self.assertIn("port_value: 8880", template_yaml,
            "Envoy base template must use port 8880 (non-privileged)")
        self.assertNotIn("port_value: 80\n", template_yaml,
            "Envoy base template must NOT use privileged port 80")

    def test_envoy_service_targets_8880(self):
        svc = _find_named(DOCS, "Service", "envoy")
        self.assertIsNotNone(svc)
        ports = svc.get("spec", {}).get("ports", [])
        http_port = next((p for p in ports if p.get("name") == "http"), None)
        self.assertIsNotNone(http_port, "envoy Service missing http port")
        self.assertEqual(http_port.get("targetPort"), 8880,
            "envoy Service targetPort must be 8880 to match container")

    def test_envoy_container_port_is_8880(self):
        dep = _find_named(DOCS, "Deployment", "envoy")
        self.assertIsNotNone(dep)
        containers = dep["spec"]["template"]["spec"]["containers"]
        envoy_c = next((c for c in containers if c["name"] == "envoy"), None)
        self.assertIsNotNone(envoy_c)
        http_port = next(
            (p for p in envoy_c.get("ports", []) if p.get("name") == "http"), None
        )
        self.assertIsNotNone(http_port)
        self.assertEqual(http_port["containerPort"], 8880)


class TestEnvoyInitContainer(unittest.TestCase):
    """Envoy init container must seed base config on first deploy."""

    def test_init_container_exists(self):
        dep = _find_named(DOCS, "Deployment", "envoy")
        self.assertIsNotNone(dep)
        init_containers = dep["spec"]["template"]["spec"].get("initContainers", [])
        seed = next((c for c in init_containers if c["name"] == "seed-base-config"), None)
        self.assertIsNotNone(seed, "envoy must have seed-base-config init container")

    def test_init_container_seeds_from_template(self):
        dep = _find_named(DOCS, "Deployment", "envoy")
        init_containers = dep["spec"]["template"]["spec"].get("initContainers", [])
        seed = next((c for c in init_containers if c["name"] == "seed-base-config"), None)
        cmd_parts = seed.get("command", [])
        script = cmd_parts[-1] if cmd_parts else ""
        self.assertIn("envoy.yaml", script,
            "Init container must reference envoy.yaml")


class TestSecretExists(unittest.TestCase):
    def test_media_stack_secrets_defined(self):
        secret = _find_named(DOCS, "Secret", "media-stack-secrets")
        self.assertIsNotNone(secret, "media-stack-secrets must be in manifest")

    def test_secret_has_required_keys(self):
        secret = _find_named(DOCS, "Secret", "media-stack-secrets")
        data = secret.get("stringData", {})
        required = ["SONARR_API_KEY", "RADARR_API_KEY", "PROWLARR_API_KEY",
                     "JELLYFIN_API_KEY", "STACK_ADMIN_PASSWORD"]
        for key in required:
            self.assertIn(key, data, f"Secret missing required key: {key}")


class TestServicePorts(unittest.TestCase):
    """Services must have matching targetPort to container port."""

    def test_all_services_have_matching_target_ports(self):
        deployments = {
            d["metadata"]["name"]: d for d in _find_by_kind(DOCS, "Deployment")
        }
        services = _find_by_kind(DOCS, "Service")
        for svc in services:
            svc_name = svc["metadata"]["name"]
            selector = svc.get("spec", {}).get("selector", {})
            if not selector:
                continue
            # Find matching deployment
            dep = None
            for d in deployments.values():
                labels = d.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
                if all(labels.get(k) == v for k, v in selector.items()):
                    dep = d
                    break
            if not dep:
                continue
            containers = dep["spec"]["template"]["spec"]["containers"]
            container_ports = set()
            for c in containers:
                for p in c.get("ports", []):
                    container_ports.add(p.get("containerPort"))
                    if p.get("name"):
                        container_ports.add(p["name"])
            for port_spec in svc.get("spec", {}).get("ports", []):
                tp = port_spec.get("targetPort")
                if tp and tp not in container_ports:
                    self.fail(
                        f"Service {svc_name} targetPort {tp} not found in "
                        f"container ports {container_ports}"
                    )


class TestDeploymentConsistency(unittest.TestCase):
    """All Deployments should have basic reliability settings."""

    def test_controller_has_recreate_strategy(self):
        dep = _find_named(DOCS, "Deployment", "media-stack-controller")
        self.assertIsNotNone(dep)
        strategy = dep["spec"].get("strategy", {}).get("type", "RollingUpdate")
        self.assertEqual(strategy, "Recreate",
            "Controller must use Recreate strategy (single-replica stateful)")

    def test_all_deployments_have_resource_limits(self):
        """At least spot-check that LimitRange exists for defaults."""
        lr = _find_by_kind(DOCS, "LimitRange")
        self.assertTrue(lr, "LimitRange should exist for default resource limits")


class TestCronJobSchedules(unittest.TestCase):
    def test_cronjobs_have_valid_schedules(self):
        for cj in _find_by_kind(DOCS, "CronJob"):
            schedule = cj.get("spec", {}).get("schedule", "")
            self.assertTrue(schedule, f"{cj['metadata']['name']} missing schedule")
            parts = schedule.split()
            self.assertEqual(len(parts), 5,
                f"{cj['metadata']['name']}: invalid cron schedule '{schedule}'")


if __name__ == "__main__":
    unittest.main()
