import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.kubernetes.rebuild_platform_adapter import (  # noqa: E402
    KubernetesRebuildPlatformAdapter,
    KubernetesRebuildPlatformConfig,
)


class KubernetesRebuildPlatformAdapterTests(unittest.TestCase):
    def _adapter(self):
        return KubernetesRebuildPlatformAdapter(
            cfg=KubernetesRebuildPlatformConfig(namespace="media-dev"),
            namespace_service=mock.Mock(),
            manifest_apply_service=mock.Mock(),
            ingress_service=mock.Mock(),
            deployments_wait_service=mock.Mock(),
            smoke_test_service=mock.Mock(),
            secret_preservation_service=mock.Mock(),
            info=mock.Mock(),
            run_kubectl=mock.Mock(),
        )

    def test_environment_ref_uses_namespace_as_environment_id(self):
        adapter = self._adapter()
        self.assertEqual(adapter.environment.environment_id, "media-dev")
        self.assertEqual(adapter.environment.target, "k8s")

    def test_delete_environment_optional_delegates_to_namespace_service(self):
        adapter = self._adapter()
        adapter.namespace_service.delete_namespace_optional.return_value = True
        self.assertTrue(adapter.delete_environment_optional("1"))
        adapter.namespace_service.delete_namespace_optional.assert_called_once_with("1")

    def test_apply_environment_definition_delegates(self):
        adapter = self._adapter()
        adapter.apply_environment_definition()
        adapter.manifest_apply_service.apply_manifests_for_profile.assert_called_once()

    def test_reconcile_edge_routing_delegates(self):
        adapter = self._adapter()
        adapter.ingress_service.patch_ingress_class.return_value = True
        self.assertTrue(adapter.reconcile_edge_routing())
        adapter.ingress_service.patch_ingress_class.assert_called_once()

    def test_wait_for_workloads_delegates(self):
        adapter = self._adapter()
        adapter.wait_for_workloads()
        adapter.deployments_wait_service.wait_for_deployments.assert_called_once()

    def test_run_smoke_test_delegates(self):
        adapter = self._adapter()
        adapter.smoke_test_service.run_smoke_test.return_value = "192.168.1.60"
        self.assertEqual(adapter.run_smoke_test(), "192.168.1.60")
        adapter.smoke_test_service.run_smoke_test.assert_called_once()

    def test_print_workload_status_prints_pods_via_kubectl(self):
        adapter = self._adapter()
        adapter.print_workload_status()
        adapter.info.assert_called_once_with("Final pod status:")
        adapter.run_kubectl.assert_called_once_with(["-n", "media-dev", "get", "pods"])

    def test_backup_secret_values_delegates(self):
        adapter = self._adapter()
        adapter.secret_preservation_service.backup_existing_values.return_value = {
            "STACK_ADMIN_USERNAME": "admin"
        }
        values = adapter.backup_secret_values("1")
        self.assertEqual(values, {"STACK_ADMIN_USERNAME": "admin"})
        adapter.secret_preservation_service.backup_existing_values.assert_called_once_with("1")

    def test_restore_secret_values_delegates(self):
        adapter = self._adapter()
        values = {"STACK_ADMIN_PASSWORD": "pass"}
        adapter.restore_secret_values(values)
        adapter.secret_preservation_service.restore_values.assert_called_once_with(values)


if __name__ == "__main__":
    unittest.main()
