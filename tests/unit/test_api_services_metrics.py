"""Tests for media_stack.api.services.metrics — Prometheus, Envoy, RSS, Grafana."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.metrics as metrics_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cache(health_data=None):
    """Return a mock cache whose .get("health", ...) returns *health_data*."""
    cache = MagicMock()
    cache.get = MagicMock(return_value=health_data)
    return cache


def _make_health(services_dict, healthy=None, total=None):
    """Build a health response dict from a simplified services mapping."""
    if healthy is None:
        healthy = sum(1 for v in services_dict.values() if v.get("status") == "ok")
    if total is None:
        total = len(services_dict)
    return {"services": services_dict, "healthy": healthy, "total": total}


def _make_state(action_history=None):
    """Return a mock state object with a to_dict() method."""
    state = MagicMock()
    state.to_dict.return_value = {"action_history": action_history or []}
    return state


# ---------------------------------------------------------------------------
# get_prometheus_metrics
# ---------------------------------------------------------------------------


class TestGetPrometheusMetricsAllUp(unittest.TestCase):
    """All services healthy produces correct Prometheus text."""

    @patch.object(metrics_mod, "probe_services")
    def test_all_services_up(self, mock_probe):
        services = {
            "sonarr": {"status": "ok", "ms": 12},
            "radarr": {"status": "ok", "ms": 8},
        }
        mock_probe.return_value = _make_health(services)
        cache = MagicMock()

        text = metrics_mod.get_prometheus_metrics(cache)

        self.assertIn('media_stack_service_up{service="radarr"} 1', text)
        self.assertIn('media_stack_service_up{service="sonarr"} 1', text)
        self.assertIn("media_stack_healthy_total 2", text)
        self.assertIn("media_stack_total_services 2", text)


class TestGetPrometheusMetricsMixedState(unittest.TestCase):
    """Mixed up/down services report correct gauge values."""

    @patch.object(metrics_mod, "probe_services")
    def test_mixed_up_and_down(self, mock_probe):
        services = {
            "sonarr": {"status": "ok", "ms": 10},
            "radarr": {"status": "error", "ms": 0},
            "lidarr": {"status": "ok", "ms": 25},
        }
        mock_probe.return_value = _make_health(services, healthy=2, total=3)
        cache = MagicMock()

        text = metrics_mod.get_prometheus_metrics(cache)

        self.assertIn('media_stack_service_up{service="sonarr"} 1', text)
        self.assertIn('media_stack_service_up{service="radarr"} 0', text)
        self.assertIn('media_stack_service_up{service="lidarr"} 1', text)
        self.assertIn("media_stack_healthy_total 2", text)
        self.assertIn("media_stack_total_services 3", text)


class TestGetPrometheusMetricsFormat(unittest.TestCase):
    """Output follows Prometheus text exposition format."""

    @patch.object(metrics_mod, "probe_services")
    def test_prometheus_format_headers(self, mock_probe):
        services = {"jellyfin": {"status": "ok", "ms": 5}}
        mock_probe.return_value = _make_health(services)
        cache = MagicMock()

        text = metrics_mod.get_prometheus_metrics(cache)

        self.assertIn("# HELP media_stack_service_up", text)
        self.assertIn("# TYPE media_stack_service_up gauge", text)
        self.assertIn("# HELP media_stack_service_latency_ms", text)
        self.assertIn("# TYPE media_stack_service_latency_ms gauge", text)
        self.assertIn("# HELP media_stack_healthy_total", text)
        self.assertIn("# TYPE media_stack_healthy_total gauge", text)
        self.assertIn("# HELP media_stack_total_services", text)
        self.assertIn("# TYPE media_stack_total_services gauge", text)
        # Must end with a newline
        self.assertTrue(text.endswith("\n"))

    @patch.object(metrics_mod, "probe_services")
    def test_latency_only_for_nonzero(self, mock_probe):
        services = {
            "sonarr": {"status": "ok", "ms": 15},
            "radarr": {"status": "error", "ms": 0},
        }
        mock_probe.return_value = _make_health(services)
        cache = MagicMock()

        text = metrics_mod.get_prometheus_metrics(cache)

        self.assertIn('media_stack_service_latency_ms{service="sonarr"} 15', text)
        self.assertNotIn('media_stack_service_latency_ms{service="radarr"}', text)


# ---------------------------------------------------------------------------
# get_envoy_stats
# ---------------------------------------------------------------------------


class TestGetEnvoyStatsSuccess(unittest.TestCase):
    """Successful fetch returns filtered stats."""

    @patch("urllib.request.urlopen")
    def test_filtered_stats(self, mock_urlopen):
        raw_stats = [
            {"name": "http.ingress.downstream_cx_total", "value": 100},
            {"name": "http.ingress.downstream_rq_total", "value": 200},
            {"name": "http.ingress.downstream_rq_2xx", "value": 180},
            {"name": "http.ingress.downstream_rq_4xx", "value": 15},
            {"name": "http.ingress.downstream_rq_5xx", "value": 5},
            {"name": "cluster.upstream_cx_total", "value": 50},
            {"name": "cluster.unrelated_metric", "value": 999},
        ]
        payload = json.dumps({"stats": raw_stats}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = metrics_mod.get_envoy_stats()

        self.assertIn("stats", result)
        self.assertEqual(result["raw_count"], 7)
        # The unrelated metric must be filtered out
        self.assertNotIn("cluster.unrelated_metric", result["stats"])
        self.assertEqual(result["stats"]["http.ingress.downstream_cx_total"], 100)
        self.assertEqual(result["stats"]["http.ingress.downstream_rq_2xx"], 180)
        self.assertEqual(result["stats"]["http.ingress.downstream_rq_5xx"], 5)
        self.assertEqual(result["stats"]["cluster.upstream_cx_total"], 50)
        self.assertNotIn("error", result)


class TestGetEnvoyStatsError(unittest.TestCase):
    """Connection error returns empty stats with an error message."""

    @patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused"))
    def test_connection_refused(self, _mock):
        result = metrics_mod.get_envoy_stats()

        self.assertEqual(result["stats"], {})
        self.assertIn("error", result)
        self.assertIn("refused", result["error"])


# ---------------------------------------------------------------------------
# get_rss_feed
# ---------------------------------------------------------------------------


class TestGetRssFeedStructure(unittest.TestCase):
    """RSS output is well-formed XML with expected elements."""

    def test_rss_with_actions_and_health(self):
        history = [
            {"name": "bootstrap", "elapsed_seconds": 12},
            {"name": "restart", "elapsed_seconds": 3, "error": "timeout"},
        ]
        state = _make_state(history)
        cache = _make_cache({"healthy": 5, "total": 7})

        xml_text = metrics_mod.get_rss_feed(state, cache)

        self.assertTrue(xml_text.startswith("<?xml"))
        root = ElementTree.fromstring(xml_text)
        self.assertEqual(root.tag, "rss")
        self.assertEqual(root.attrib.get("version"), "2.0")
        channel = root.find("channel")
        self.assertIsNotNone(channel)
        self.assertEqual(channel.find("title").text, "Media Stack Controller")
        items = channel.findall("item")
        # 1 health item + 2 action items
        self.assertEqual(len(items), 3)


class TestGetRssFeedEmptyHistory(unittest.TestCase):
    """Empty action_history still produces valid XML with health item."""

    def test_empty_history_with_health(self):
        state = _make_state([])
        cache = _make_cache({"healthy": 3, "total": 3})

        xml_text = metrics_mod.get_rss_feed(state, cache)

        root = ElementTree.fromstring(xml_text)
        items = root.find("channel").findall("item")
        self.assertEqual(len(items), 1)
        self.assertIn("Health:", items[0].find("title").text)

    def test_empty_history_no_health_cache(self):
        state = _make_state([])
        cache = _make_cache(None)

        xml_text = metrics_mod.get_rss_feed(state, cache)

        root = ElementTree.fromstring(xml_text)
        items = root.find("channel").findall("item")
        self.assertEqual(len(items), 0)


class TestGetRssFeedErrorActions(unittest.TestCase):
    """Actions with errors show 'error' status and include error detail."""

    def test_error_action_content(self):
        history = [{"name": "push-indexers", "elapsed_seconds": 1, "error": "connection lost"}]
        state = _make_state(history)
        cache = _make_cache(None)

        xml_text = metrics_mod.get_rss_feed(state, cache)

        root = ElementTree.fromstring(xml_text)
        item = root.find("channel").find("item")
        self.assertIn("error", item.find("title").text)
        self.assertEqual(item.find("category").text, "error")
        self.assertIn("connection lost", item.find("description").text)

    def test_successful_action_content(self):
        history = [{"name": "rebuild", "elapsed_seconds": 45}]
        state = _make_state(history)
        cache = _make_cache(None)

        xml_text = metrics_mod.get_rss_feed(state, cache)

        root = ElementTree.fromstring(xml_text)
        item = root.find("channel").find("item")
        self.assertIn("complete", item.find("title").text)
        self.assertEqual(item.find("category").text, "complete")


# ---------------------------------------------------------------------------
# get_grafana_dashboard
# ---------------------------------------------------------------------------


class TestGetGrafanaDashboardStructure(unittest.TestCase):
    """Grafana dashboard JSON has required top-level keys and panels."""

    def test_top_level_keys(self):
        result = metrics_mod.get_grafana_dashboard()

        self.assertIn("dashboard", result)
        self.assertIn("overwrite", result)
        self.assertTrue(result["overwrite"])

    def test_dashboard_metadata(self):
        dash = metrics_mod.get_grafana_dashboard()["dashboard"]

        self.assertEqual(dash["title"], "Media Stack")
        self.assertIn("time", dash)
        self.assertEqual(dash["time"]["from"], "now-6h")
        self.assertEqual(dash["time"]["to"], "now")
        self.assertEqual(dash["refresh"], "30s")


class TestGetGrafanaDashboardPanels(unittest.TestCase):
    """Panels include the expected types and targets."""

    def test_panel_types(self):
        panels = metrics_mod.get_grafana_dashboard()["dashboard"]["panels"]

        types = [p["type"] for p in panels]
        self.assertIn("stat", types)
        self.assertIn("timeseries", types)

    def test_stat_panel_target(self):
        panels = metrics_mod.get_grafana_dashboard()["dashboard"]["panels"]
        stat_panel = next(p for p in panels if p["type"] == "stat")

        self.assertEqual(stat_panel["title"], "Services Up")
        self.assertEqual(stat_panel["targets"][0]["expr"], "media_stack_healthy_total")

    def test_timeseries_panel_target(self):
        panels = metrics_mod.get_grafana_dashboard()["dashboard"]["panels"]
        ts_panel = next(p for p in panels if p["type"] == "timeseries")

        self.assertEqual(ts_panel["title"], "Service Latency")
        self.assertEqual(ts_panel["targets"][0]["expr"], "media_stack_service_latency_ms")
        self.assertEqual(ts_panel["targets"][0]["legendFormat"], "{{service}}")


if __name__ == "__main__":
    unittest.main()
