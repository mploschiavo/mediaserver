"""Validate k8s/ source manifests for deployment reliability.

Unlike test_k8s_deploy_manifest.py which validates dist/k8s-deploy.yaml
(a build artifact that can go stale), this tests the actual source files
in k8s/ that get applied via kustomize.

Catches:
  - ConfigMap/Secret volumes that reference resources not defined in any manifest
  - Missing optional: true on ConfigMap volumes that may not exist at deploy time
  - Volume name mismatches between volumeMounts and volumes
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
K8S_DIR = ROOT / "k8s"


def _load_all_k8s_documents() -> list[dict]:
    """Load all YAML documents from k8s/ source files."""
    docs = []
    if not K8S_DIR.is_dir():
        return docs
    # Read kustomization to know which files are included
    kustomization = K8S_DIR / "kustomization.yaml"
    if kustomization.is_file():
        kust = yaml.safe_load(kustomization.read_text()) or {}
        resources = kust.get("resources", [])
    else:
        resources = [f.name for f in K8S_DIR.glob("*.yaml") if f.name != "kustomization.yaml"]

    for resource_file in resources:
        path = K8S_DIR / resource_file
        if not path.is_file():
            continue
        try:
            for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
                if doc and isinstance(doc, dict):
                    doc["_source_file"] = resource_file
                    docs.append(doc)
        except Exception:
            continue
    return docs


def _find_by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == kind]


def _extract_pod_specs(docs: list[dict]) -> list[tuple[str, str, dict]]:
    """Extract (workload_kind, workload_name, pod_spec) from all workloads."""
    specs = []
    for doc in docs:
        kind = doc.get("kind", "")
        name = doc.get("metadata", {}).get("name", "?")
        if kind == "Deployment":
            pod_spec = doc.get("spec", {}).get("template", {}).get("spec", {})
            specs.append((kind, name, pod_spec))
        elif kind == "CronJob":
            pod_spec = (doc.get("spec", {}).get("jobTemplate", {})
                        .get("spec", {}).get("template", {}).get("spec", {}))
            specs.append((kind, name, pod_spec))
    return specs


DOCS = _load_all_k8s_documents()


class TestSourceFilesLoad(unittest.TestCase):
    def test_k8s_directory_exists(self):
        self.assertTrue(K8S_DIR.is_dir(), f"Missing {K8S_DIR}")

    def test_has_kustomization(self):
        self.assertTrue((K8S_DIR / "kustomization.yaml").is_file())

    def test_all_kustomize_resources_exist(self):
        kust = yaml.safe_load((K8S_DIR / "kustomization.yaml").read_text()) or {}
        for resource in kust.get("resources", []):
            path = K8S_DIR / resource
            self.assertTrue(path.is_file(), f"kustomization references missing file: {resource}")

    def test_has_documents(self):
        self.assertGreater(len(DOCS), 5, "Too few documents in k8s/ manifests")


class TestConfigMapReferences(unittest.TestCase):
    """Every ConfigMap volume must either be defined or marked optional."""

    def _get_defined_configmaps(self) -> set[str]:
        return {
            d["metadata"]["name"]
            for d in _find_by_kind(DOCS, "ConfigMap")
            if d.get("metadata", {}).get("name")
        }

    def test_all_configmap_volumes_resolvable(self):
        """ConfigMap volumes must be defined in manifests OR marked optional."""
        defined = self._get_defined_configmaps()
        problems = []
        for kind, name, pod_spec in _extract_pod_specs(DOCS):
            for vol in pod_spec.get("volumes", []):
                cm = vol.get("configMap")
                if not cm:
                    continue
                cm_name = cm.get("name", "")
                is_optional = cm.get("optional", False)
                if cm_name not in defined and not is_optional:
                    problems.append(
                        f"{kind}/{name}: configMap volume '{cm_name}' "
                        f"not defined in manifests and not optional"
                    )
        self.assertFalse(
            problems,
            "ConfigMap volumes that will cause ContainerCreating failures:\n"
            + "\n".join(f"  - {p}" for p in problems),
        )

    def test_no_phantom_configmaps(self):
        """Flag ConfigMap volumes that are optional but never defined anywhere.

        These are dead weight — the volume mounts as an empty directory.
        Not a failure, but a code smell worth tracking.
        """
        defined = self._get_defined_configmaps()
        phantoms = []
        for kind, name, pod_spec in _extract_pod_specs(DOCS):
            for vol in pod_spec.get("volumes", []):
                cm = vol.get("configMap")
                if not cm:
                    continue
                cm_name = cm.get("name", "")
                is_optional = cm.get("optional", False)
                if cm_name not in defined and is_optional:
                    phantoms.append(f"{kind}/{name}: optional configMap '{cm_name}' is never created")
        # Log but don't fail — these are cleanup candidates
        if phantoms:
            import warnings
            warnings.warn(
                "Phantom ConfigMaps (optional but never defined):\n"
                + "\n".join(f"  - {p}" for p in phantoms)
            )


class TestVolumeMountConsistency(unittest.TestCase):
    """Every volumeMount must reference a volume that exists in the pod spec."""

    def test_all_volume_mounts_have_matching_volumes(self):
        problems = []
        for kind, name, pod_spec in _extract_pod_specs(DOCS):
            volume_names = {v.get("name") for v in pod_spec.get("volumes", [])}
            all_containers = (
                pod_spec.get("initContainers", []) + pod_spec.get("containers", [])
            )
            for container in all_containers:
                c_name = container.get("name", "?")
                for vm in container.get("volumeMounts", []):
                    vm_name = vm.get("name", "")
                    if vm_name not in volume_names:
                        problems.append(
                            f"{kind}/{name}/{c_name}: volumeMount '{vm_name}' "
                            f"has no matching volume definition"
                        )
        self.assertFalse(
            problems,
            "Volume mount mismatches:\n" + "\n".join(f"  - {p}" for p in problems),
        )


class TestPVCReferences(unittest.TestCase):
    """PVC volumes should reference PVCs defined in the manifests."""

    def test_all_pvc_volumes_are_defined(self):
        defined_pvcs = {
            d["metadata"]["name"]
            for d in _find_by_kind(DOCS, "PersistentVolumeClaim")
            if d.get("metadata", {}).get("name")
        }
        if not defined_pvcs:
            self.skipTest("No PVCs defined in manifests")

        referenced = set()
        for kind, name, pod_spec in _extract_pod_specs(DOCS):
            for vol in pod_spec.get("volumes", []):
                pvc = vol.get("persistentVolumeClaim")
                if pvc:
                    referenced.add(pvc.get("claimName", ""))

        missing = referenced - defined_pvcs
        self.assertFalse(
            missing,
            f"PVC volumes referenced but not defined: {missing}",
        )


class TestSecretReferences(unittest.TestCase):
    """Secret references should point to secrets defined in manifests."""

    def test_secret_volumes_are_defined(self):
        defined_secrets = {
            d["metadata"]["name"]
            for d in _find_by_kind(DOCS, "Secret")
            if d.get("metadata", {}).get("name")
        }
        if not defined_secrets:
            self.skipTest("No Secrets defined in manifests")

        problems = []
        for kind, name, pod_spec in _extract_pod_specs(DOCS):
            for vol in pod_spec.get("volumes", []):
                secret = vol.get("secret")
                if secret:
                    secret_name = secret.get("secretName", "")
                    is_optional = secret.get("optional", False)
                    if secret_name not in defined_secrets and not is_optional:
                        problems.append(f"{kind}/{name}: secret '{secret_name}' not defined")

        self.assertFalse(
            problems,
            "Secret volumes not defined:\n" + "\n".join(f"  - {p}" for p in problems),
        )


if __name__ == "__main__":
    unittest.main()
