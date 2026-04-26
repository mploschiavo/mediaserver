"""Tests for container aggregate metrics in ops.py."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.ops import _aggregate_metrics  # noqa: E402


class TestAggregateMetrics(unittest.TestCase):
    def test_empty_metrics(self):
        result = _aggregate_metrics([])
        self.assertEqual(result["cpu_millicores"], 0)
        self.assertEqual(result["memory_mi"], 0)
        self.assertEqual(result["container_count"], 0)

    def test_single_container_millicores(self):
        metrics = [{"pod": "sonarr", "cpu": "100m", "memory": "256Mi"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_millicores"], 100)
        self.assertEqual(result["memory_mi"], 256)

    def test_multiple_containers_sum(self):
        metrics = [
            {"pod": "sonarr", "cpu": "100m", "memory": "256Mi"},
            {"pod": "radarr", "cpu": "200m", "memory": "512Mi"},
        ]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_millicores"], 300)
        self.assertEqual(result["memory_mi"], 768)
        self.assertEqual(result["container_count"], 2)

    def test_nanocores_conversion(self):
        metrics = [{"pod": "test", "cpu": "500000000n", "memory": "0"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_millicores"], 500)

    def test_float_cores_conversion(self):
        metrics = [{"pod": "test", "cpu": "1.5", "memory": "0"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_millicores"], 1500)

    def test_kilobytes_memory(self):
        metrics = [{"pod": "test", "cpu": "0", "memory": "1048576Ki"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["memory_mi"], 1024)

    def test_gigabytes_memory(self):
        metrics = [{"pod": "test", "cpu": "0", "memory": "2Gi"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["memory_mi"], 2048)

    def test_raw_bytes_memory(self):
        metrics = [{"pod": "test", "cpu": "0", "memory": "1048576"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["memory_mi"], 1)

    def test_cpu_display_under_1000(self):
        metrics = [{"pod": "test", "cpu": "500m", "memory": "0"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_display"], "500m")

    def test_cpu_display_over_1000(self):
        metrics = [
            {"pod": "a", "cpu": "600m", "memory": "0"},
            {"pod": "b", "cpu": "600m", "memory": "0"},
        ]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_display"], "1.2 cores")

    def test_memory_display_under_1024(self):
        metrics = [{"pod": "test", "cpu": "0", "memory": "512Mi"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["memory_display"], "512Mi")

    def test_memory_display_over_1024(self):
        metrics = [
            {"pod": "a", "cpu": "0", "memory": "600Mi"},
            {"pod": "b", "cpu": "0", "memory": "600Mi"},
        ]
        result = _aggregate_metrics(metrics)
        self.assertIn("Gi", result["memory_display"])

    def test_zero_values(self):
        metrics = [{"pod": "test", "cpu": "0", "memory": "0"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_millicores"], 0)
        self.assertEqual(result["memory_mi"], 0)

    def test_missing_keys_default(self):
        metrics = [{"pod": "test"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_millicores"], 0)
        self.assertEqual(result["memory_mi"], 0)

    def test_mixed_cpu_units(self):
        metrics = [
            {"pod": "a", "cpu": "100m", "memory": "0"},
            {"pod": "b", "cpu": "0.2", "memory": "0"},
            {"pod": "c", "cpu": "300000000n", "memory": "0"},
        ]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_millicores"], 600)

    def test_mixed_memory_units(self):
        metrics = [
            {"pod": "a", "cpu": "0", "memory": "256Mi"},
            {"pod": "b", "cpu": "0", "memory": "1Gi"},
            {"pod": "c", "cpu": "0", "memory": "512000Ki"},
        ]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["memory_mi"], 256 + 1024 + 500)

    def test_container_count(self):
        metrics = [{"pod": f"p{i}", "cpu": "10m", "memory": "10Mi"} for i in range(5)]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["container_count"], 5)

    @patch.dict(os.environ, {"K8S_NAMESPACE": ""})
    @patch("docker.from_env")
    def test_compose_containers_include_totals(self, mock_docker):
        from media_stack.api.services.ops import _get_compose_containers
        c = MagicMock()
        c.name = "sonarr"
        c.status = "running"
        c.image.tags = ["sonarr:latest"]
        c.image.short_id = "sha256:abc"
        c.stats.return_value = {
            "cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 10000},
            "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 9000},
            "memory_stats": {"usage": 268435456},
        }
        mock_docker.return_value.containers.list.return_value = [c]
        result = _get_compose_containers()
        self.assertIn("totals", result)
        self.assertIn("cpu_millicores", result["totals"])

    def test_large_cluster_metrics(self):
        metrics = [{"pod": f"p{i}", "cpu": "500m", "memory": "1Gi"} for i in range(20)]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_millicores"], 10000)
        self.assertEqual(result["memory_mi"], 20480)
        self.assertIn("cores", result["cpu_display"])

    def test_non_numeric_cpu_ignored(self):
        metrics = [{"pod": "test", "cpu": "abc", "memory": "256Mi"}]
        result = _aggregate_metrics(metrics)
        self.assertEqual(result["cpu_millicores"], 0)
        self.assertEqual(result["memory_mi"], 256)


if __name__ == "__main__":
    unittest.main()
