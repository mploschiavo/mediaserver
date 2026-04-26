import unittest
from pathlib import Path


class RuntimeManifestsPvcOnlyTests(unittest.TestCase):
    def test_runtime_manifests_do_not_use_hostpath(self):
        root = Path(__file__).resolve().parents[3]
        manifests = [
            root / "k8s" / "core.yaml",
            root / "k8s" / "controller.yaml",
            root / "k8s" / "optional.yaml",
            root / "k8s" / "envoy.yaml",
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
