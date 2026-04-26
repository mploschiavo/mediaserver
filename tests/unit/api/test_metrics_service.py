"""Tests for metrics services: Prometheus, RSS feed, Grafana, Envoy stats."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.metrics as metrics_mod  # noqa: E402


class _FakeCache:
    def __init__(self, data=None):
        self._data = data
    def get(self, key, ttl=60):
        return self._data
    def set(self, key, value):
        self._data = value


class TestGetPrometheusMetrics(unittest.TestCase):
    @patch("media_stack.api.services.metrics.probe_services")
    def test_generates_prometheus_format(self, mock_probe):
        mock_probe.return_value = {
            "services": {"sonarr": {"status": "ok", "ms": 42}, "radarr": {"status": "error", "ms": 0}},
            "healthy": 1, "total": 2,
        }
        result = metrics_mod.get_prometheus_metrics(_FakeCache())
        self.assertIn("media_stack_service_up", result)
        self.assertIn('service="sonarr"} 1', result)
        self.assertIn('service="radarr"} 0', result)

    @patch("media_stack.api.services.metrics.probe_services")
    def test_includes_latency(self, mock_probe):
        mock_probe.return_value = {
            "services": {"sonarr": {"status": "ok", "ms": 55}},
            "healthy": 1, "total": 1,
        }
        result = metrics_mod.get_prometheus_metrics(_FakeCache())
        self.assertIn("media_stack_service_latency_ms", result)
        self.assertIn("55", result)

    @patch("media_stack.api.services.metrics.probe_services")
    def test_includes_totals(self, mock_probe):
        mock_probe.return_value = {"services": {}, "healthy": 5, "total": 10}
        result = metrics_mod.get_prometheus_metrics(_FakeCache())
        self.assertIn("media_stack_healthy_total 5", result)
        self.assertIn("media_stack_total_services 10", result)

    @patch("media_stack.api.services.metrics.probe_services")
    def test_ends_with_newline(self, mock_probe):
        mock_probe.return_value = {"services": {}, "healthy": 0, "total": 0}
        result = metrics_mod.get_prometheus_metrics(_FakeCache())
        self.assertTrue(result.endswith("\n"))

    @patch("media_stack.api.services.metrics.probe_services")
    def test_zero_latency_excluded(self, mock_probe):
        mock_probe.return_value = {
            "services": {"app": {"status": "ok", "ms": 0}},
            "healthy": 1, "total": 1,
        }
        result = metrics_mod.get_prometheus_metrics(_FakeCache())
        self.assertNotIn('service="app"} 0\n', result.split("latency")[1] if "latency" in result else "")


class TestGetEnvoyStats(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_parses_envoy_stats(self, mock_urlopen):
        import json
        resp = MagicMock()
        resp.read.return_value = json.dumps({"stats": [
            {"name": "http.ingress.downstream_rq_total", "value": 100},
            {"name": "http.ingress.downstream_rq_2xx", "value": 90},
            {"name": "cluster.unrelated", "value": 5},
        ]}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        result = metrics_mod.get_envoy_stats()
        self.assertIn("downstream_rq_total", str(result["stats"]))
        self.assertEqual(result["raw_count"], 3)

    @patch("urllib.request.urlopen", side_effect=Exception("not reachable"))
    def test_envoy_not_reachable(self, _):
        result = metrics_mod.get_envoy_stats()
        self.assertEqual(result["stats"], {})
        self.assertIn("error", result)


class TestGetRssFeed(unittest.TestCase):
    def test_generates_valid_xml(self):
        state = MagicMock()
        state.to_dict.return_value = {
            "action_history": [
                {"name": "bootstrap", "error": None, "elapsed_seconds": 5.2},
                {"name": "reconcile", "error": "timeout", "elapsed_seconds": 600},
            ]
        }
        cache = _FakeCache({"healthy": 7, "total": 7})
        result = metrics_mod.get_rss_feed(state, cache)
        self.assertIn("<?xml", result)
        self.assertIn("<rss", result)
        self.assertIn("bootstrap", result)
        self.assertIn("reconcile", result)

    def test_empty_history(self):
        state = MagicMock()
        state.to_dict.return_value = {"action_history": []}
        result = metrics_mod.get_rss_feed(state, _FakeCache())
        self.assertIn("<rss", result)

    def test_health_item_included(self):
        state = MagicMock()
        state.to_dict.return_value = {"action_history": []}
        cache = _FakeCache({"healthy": 5, "total": 7})
        result = metrics_mod.get_rss_feed(state, cache)
        self.assertIn("5/7", result)

    def test_error_actions_marked(self):
        state = MagicMock()
        state.to_dict.return_value = {
            "action_history": [{"name": "x", "error": "boom", "elapsed_seconds": 1}]
        }
        result = metrics_mod.get_rss_feed(state, _FakeCache())
        self.assertIn("error", result)


class TestGetGrafanaDashboard(unittest.TestCase):
    def test_returns_dashboard_structure(self):
        result = metrics_mod.get_grafana_dashboard()
        self.assertIn("dashboard", result)
        self.assertIn("panels", result["dashboard"])
        self.assertIn("title", result["dashboard"])

    def test_has_panels(self):
        result = metrics_mod.get_grafana_dashboard()
        panels = result["dashboard"]["panels"]
        self.assertGreater(len(panels), 0)

    def test_has_stat_panel(self):
        result = metrics_mod.get_grafana_dashboard()
        types = [p["type"] for p in result["dashboard"]["panels"]]
        self.assertIn("stat", types)

    def test_has_timeseries_panel(self):
        result = metrics_mod.get_grafana_dashboard()
        types = [p["type"] for p in result["dashboard"]["panels"]]
        self.assertIn("timeseries", types)

    def test_overwrite_flag(self):
        result = metrics_mod.get_grafana_dashboard()
        self.assertTrue(result["overwrite"])


if __name__ == "__main__":
    unittest.main()
