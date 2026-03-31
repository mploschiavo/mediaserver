import unittest
from pathlib import Path


class RuntimeManifestsPvcOnlyTests(unittest.TestCase):
    def test_runtime_manifests_do_not_use_hostpath(self):
        root = Path(__file__).resolve().parents[2]
        manifests = [
            root / "k8s" / "core.yaml",
            root / "k8s" / "bootstrap-job.yaml",
            root / "k8s" / "optional.yaml",
            root / "k8s" / "prowlarr-auto-indexers-job.yaml",
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
