import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.compose_rebuild_platform_adapter import (  # noqa: E402
    ComposeRebuildPlatformAdapter,
    ComposeRebuildPlatformConfig,
)


class ComposeRebuildPlatformAdapterTests(unittest.TestCase):
    def _adapter(self) -> ComposeRebuildPlatformAdapter:
        return ComposeRebuildPlatformAdapter(
            cfg=ComposeRebuildPlatformConfig(environment_id="media-dev"),
            info=mock.Mock(),
        )

    def test_environment_ref_uses_environment_id(self):
        adapter = self._adapter()
        self.assertEqual(adapter.environment.environment_id, "media-dev")
        self.assertEqual(adapter.environment.target, "compose")

    def test_delete_environment_optional_is_skipped_when_not_requested(self):
        adapter = self._adapter()
        self.assertFalse(adapter.delete_environment_optional("0"))

    def test_delete_environment_optional_requires_wired_support_when_enabled(self):
        adapter = self._adapter()
        with self.assertRaisesRegex(
            RuntimeError,
            "Compose rebuild target is recognized but not wired",
        ):
            adapter.delete_environment_optional("1")

    def test_apply_environment_definition_requires_wired_support(self):
        adapter = self._adapter()
        with self.assertRaisesRegex(
            RuntimeError,
            "Compose rebuild target is recognized but not wired",
        ):
            adapter.apply_environment_definition()

    def test_reconcile_edge_routing_returns_skipped(self):
        adapter = self._adapter()
        self.assertFalse(adapter.reconcile_edge_routing())
        adapter.info.assert_called_once_with("Compose target: ingress-class patch skipped.")

    def test_run_smoke_test_returns_empty_result(self):
        adapter = self._adapter()
        self.assertEqual(adapter.run_smoke_test(), "")
        adapter.info.assert_called_once_with("Compose target: smoke test skipped.")

    def test_wait_for_workloads_requires_wired_support(self):
        adapter = self._adapter()
        with self.assertRaisesRegex(
            RuntimeError,
            "Compose rebuild target is recognized but not wired",
        ):
            adapter.wait_for_workloads()

    def test_print_workload_status_logs_placeholder(self):
        adapter = self._adapter()
        adapter.print_workload_status()
        adapter.info.assert_called_once_with(
            "Compose target: workload status collection is not configured."
        )


if __name__ == "__main__":
    unittest.main()
