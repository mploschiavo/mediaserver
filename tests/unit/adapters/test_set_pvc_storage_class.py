import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

SPEC = importlib.util.spec_from_file_location(
    "set_pvc_storage_class", ROOT / "src" / "media_stack" / "cli" / "commands" / "set_pvc_storage_class_main.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SetPvcStorageClassTransformTests(unittest.TestCase):
    def test_set_storage_class_adds_to_all_pvc_docs(self):
        source = """apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: a
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: b
spec:
  storageClassName: old-class
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 2Gi
"""
        rendered = MODULE.transform_storage_class_manifest(
            source,
            class_name="fast-ssd",
            clear_mode=False,
        )
        self.assertIn("storageClassName: fast-ssd", rendered)
        self.assertNotIn("storageClassName: old-class", rendered)
        self.assertIn("kind: Deployment", rendered)

    def test_clear_storage_class_removes_from_pvc_docs_only(self):
        source = """apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: a
spec:
  storageClassName: keep-out
  resources:
    requests:
      storage: 1Gi
---
kind: ConfigMap
metadata:
  name: x
data:
  storageClassName: do-not-touch
"""
        rendered = MODULE.transform_storage_class_manifest(
            source,
            class_name="ignored",
            clear_mode=True,
        )
        self.assertNotIn("spec:\n  storageClassName:", rendered)
        self.assertIn("storageClassName: do-not-touch", rendered)


if __name__ == "__main__":
    unittest.main()
