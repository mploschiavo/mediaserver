"""Tests for metrics services: Prometheus, RSS feed, Grafana, Envoy stats."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
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
    """ADR-0005 Phase 5c.4c: ``get_rss_feed`` reads
    ``framework.get_job_history()`` instead of the retired
    ``state.action_history`` field. Each batch entry's ``jobs``
    map carries the same per-row payload (``elapsed`` /
    ``error``) the legacy ``ActionRecord.to_dict`` exposed.
    """

    @staticmethod
    def _state_stub():
        state = MagicMock()
        state.to_dict.return_value = {}
        return state

    @patch("media_stack.application.jobs.framework.get_job_history")
    def test_generates_valid_xml(self, mock_history):
        mock_history.return_value = [{
            "ts": 1700000000.0,
            "jobs": {
                "bootstrap": {"elapsed": 5.2, "error": None},
                "reconcile": {"elapsed": 600, "error": "timeout"},
            },
        }]
        cache = _FakeCache({"healthy": 7, "total": 7})
        result = metrics_mod.get_rss_feed(self._state_stub(), cache)
        self.assertIn("<?xml", result)
        self.assertIn("<rss", result)
        self.assertIn("bootstrap", result)
        self.assertIn("reconcile", result)

    @patch("media_stack.application.jobs.framework.get_job_history")
    def test_empty_history(self, mock_history):
        mock_history.return_value = []
        result = metrics_mod.get_rss_feed(self._state_stub(), _FakeCache())
        self.assertIn("<rss", result)

    @patch("media_stack.application.jobs.framework.get_job_history")
    def test_health_item_included(self, mock_history):
        mock_history.return_value = []
        cache = _FakeCache({"healthy": 5, "total": 7})
        result = metrics_mod.get_rss_feed(self._state_stub(), cache)
        self.assertIn("5/7", result)

    @patch("media_stack.application.jobs.framework.get_job_history")
    def test_error_actions_marked(self, mock_history):
        mock_history.return_value = [{
            "ts": 1700000000.0,
            "jobs": {"x": {"elapsed": 1, "error": "boom"}},
        }]
        result = metrics_mod.get_rss_feed(self._state_stub(), _FakeCache())
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


class TestEnvoyTimeseries(unittest.TestCase):
    """The rolling-buffer timeseries feeds the Routing tab's
    sparklines and live request-rate chart. The buffer is populated as
    a side-effect of every ``get_envoy_admin_summary()`` call."""

    def setUp(self):
        # Each test starts from a clean buffer so order doesn't matter.
        metrics_mod._timeseries_buf.clear()

    def _sample(self, total: int, _4xx: int = 0, _5xx: int = 0,
                healthy: int = 2, hosts: int = 2, active: int = 0) -> dict:
        return {
            "downstream_breakdown": {
                "total": total,
                "rq_2xx": max(0, total - _4xx - _5xx),
                "rq_4xx": _4xx,
                "rq_5xx": _5xx,
            },
            "clusters": [{"hosts": hosts, "healthy": healthy}],
            "active_connections": {"a": active},
            "tls_handshake_errors": 0,
        }

    def test_empty_buffer_returns_empty_arrays(self):
        ts = metrics_mod.get_envoy_timeseries(60)
        self.assertEqual(ts["samples"], [])
        self.assertEqual(ts["deltas"], [])

    def test_record_dedupes_same_second_samples(self):
        # Two records with identical ts (same wall-clock second) must
        # collapse to one — multiple panel tabs racing the same poll.
        with patch("media_stack.api.services.metrics.time") as mock_time:
            mock_time.time.return_value = 1_000_000
            metrics_mod._record_timeseries_sample(self._sample(100))
            metrics_mod._record_timeseries_sample(self._sample(150))
        self.assertEqual(len(metrics_mod._timeseries_buf), 1)

    def test_deltas_compute_request_rate_from_counter_differences(self):
        # Envoy counters are monotonic; rate = Δcount / Δt.
        with patch("media_stack.api.services.metrics.time") as mock_time:
            mock_time.time.return_value = 1_000_000
            metrics_mod._record_timeseries_sample(self._sample(100))
            mock_time.time.return_value = 1_000_010  # +10s
            metrics_mod._record_timeseries_sample(
                self._sample(200, _4xx=5, _5xx=5),
            )
            # Read while the patched clock is still pinned, otherwise
            # the cutoff filter (now − window) drops both samples.
            ts = metrics_mod.get_envoy_timeseries(60)
        self.assertEqual(len(ts["samples"]), 2)
        self.assertEqual(len(ts["deltas"]), 1)
        delta = ts["deltas"][0]
        # 100 requests over 10s = 10 rq/s
        self.assertAlmostEqual(delta["rq_per_s"], 10.0)
        # 4xx+5xx went 0 → 10 over 10s = 1 err/s
        self.assertAlmostEqual(delta["err_per_s"], 1.0)

    def test_window_clamps_to_minimum_60s(self):
        # window_seconds must clamp to ≥60s so nonsense values
        # (negative, zero) don't return a degenerate 1-sample series.
        with patch("media_stack.api.services.metrics.time") as mock_time:
            mock_time.time.return_value = 1_000_000
            metrics_mod._record_timeseries_sample(self._sample(100))
            mock_time.time.return_value = 1_000_005
            metrics_mod._record_timeseries_sample(self._sample(110))
            ts = metrics_mod.get_envoy_timeseries(window_seconds=0)
        # Both samples fall within the clamped 60s window.
        self.assertEqual(len(ts["samples"]), 2)
        self.assertEqual(ts["window_seconds"], 0)

    def test_window_excludes_samples_older_than_cutoff(self):
        with patch("media_stack.api.services.metrics.time") as mock_time:
            # Old sample at t=1_000_000.
            mock_time.time.return_value = 1_000_000
            metrics_mod._record_timeseries_sample(self._sample(100))
            # Current sample 1 hour later.
            mock_time.time.return_value = 1_003_700  # +1h+100s
            metrics_mod._record_timeseries_sample(self._sample(500))
            # Now query a 60s window — only the second sample should
            # land in the response.
            ts = metrics_mod.get_envoy_timeseries(window_seconds=60)
            self.assertEqual(len(ts["samples"]), 1)
            self.assertEqual(ts["samples"][0]["rq_total"], 500)

    def test_record_called_from_get_envoy_admin_summary(self):
        # The buffer should populate as a side-effect of the panel's
        # 30s polling. Mock the underlying urlopen so we don't reach
        # out to a real Envoy.
        import json
        with patch("urllib.request.urlopen") as mock_urlopen:
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "cluster_statuses": [],
                "stats": [],
                "histograms": {},
            }).encode()
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = resp
            metrics_mod.get_envoy_admin_summary()
        self.assertEqual(len(metrics_mod._timeseries_buf), 1)


if __name__ == "__main__":
    unittest.main()
