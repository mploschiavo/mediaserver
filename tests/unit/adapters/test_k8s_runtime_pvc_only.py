import unittest
from pathlib import Path


class RuntimeManifestsPvcOnlyTests(unittest.TestCase):
    def test_runtime_manifests_do_not_use_hostpath(self):
        root = Path(__file__).resolve().parents[3]
        # Manifests moved during the deploy/ reorg:
        # k8s/<name>.yaml → deploy/k8s/base/<group>/<name>.yaml.
        manifests = [
            root / "deploy" / "k8s" / "base" / "apps" / "core.yaml",
            root / "deploy" / "k8s" / "base" / "controller" / "controller.yaml",
            root / "deploy" / "k8s" / "base" / "apps" / "optional.yaml",
            root / "deploy" / "k8s" / "base" / "edge" / "envoy.yaml",
        ]
        for manifest in manifests:
            text = manifest.read_text(encoding="utf-8")
            self.assertNotIn(
                "hostPath:",
                text,
                f"hostPath must not be used in runtime manifest {manifest.name}",
            )


if __name__ == "__main__":
    unittest.main()
