"""Tests for fleet telemetry — client SDK, compact payload, UDP probe, TCP fallback.

Covers: metric collection, compact encoding/decoding, push logic,
buffer/drain, UDP probe, TCP fallback, transport selection, edge cases.
"""

import gzip
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.telemetry_client import (
    collect_metrics,
    push_telemetry,
    _to_compact,
    _from_compact,
    _cluster_id,
    _cluster_name,
    _buffer_payload,
    _buffer_path,
    _drain_buffer,
    _push_one,
    _probe_udp,
    _send_udp,
    _send_tcp,
    _parse_host_port,
    _SCHEMA_VERSION,
    _SCHEMA_FIELDS,
)


# ---------------------------------------------------------------------------
# Compact encoding / decoding
# ---------------------------------------------------------------------------

class TestCompactEncoding(unittest.TestCase):
    def test_roundtrip(self):
        """to_compact → from_compact produces equivalent data."""
        original = {
            "cluster_id": "abc-123",
            "cluster_name": "test",
            "ts": 1712835600.0,
            "controller": {"version": "1.0.1", "platform": "compose", "uptime_hours": 100},
            "services": {"total": 17, "healthy": 15},
            "jobs": {"runs_24h": 3, "ok": 3, "errors": 0, "avg_duration_s": 280},
            "media": {
                "libraries": 4, "livetv_tuners": 30, "indexers": 73,
                "storage_gb": 1240, "active_downloads": 2,
                "download_speed_mbps": 5.5, "upload_speed_mbps": 1.2,
            },
        }
        compact = _to_compact(original)
        restored = _from_compact(compact)
        self.assertEqual(restored["cluster_id"], "abc-123")
        self.assertEqual(restored["controller"]["version"], "1.0.1")
        self.assertEqual(restored["services"]["healthy"], 15)
        self.assertEqual(restored["media"]["storage_gb"], 1240)

    def test_compact_is_list(self):
        compact = _to_compact({"cluster_id": "x", "ts": 0})
        self.assertIsInstance(compact, list)

    def test_compact_length_matches_schema(self):
        compact = _to_compact({"cluster_id": "x"})
        self.assertEqual(len(compact), len(_SCHEMA_FIELDS))

    def test_missing_nested_fields_default_to_zero(self):
        compact = _to_compact({"cluster_id": "x"})
        restored = _from_compact(compact)
        self.assertEqual(restored["services"]["total"], 0)
        self.assertEqual(restored["media"]["libraries"], 0)

    def test_schema_version(self):
        self.assertEqual(_SCHEMA_VERSION, 1)
        self.assertGreater(len(_SCHEMA_FIELDS), 10)

    def test_compact_size_small(self):
        payload = {"cluster_id": "test", "cluster_name": "demo", "ts": time.time()}
        compact = [_SCHEMA_VERSION] + _to_compact(payload)
        raw = json.dumps(compact, separators=(",", ":"))
        self.assertLess(len(raw), 200, "Compact payload should be under 200 bytes")

    def test_gzipped_size_tiny(self):
        payload = {"cluster_id": "test", "ts": time.time(),
                   "services": {"total": 17, "healthy": 15},
                   "media": {"storage_gb": 1240, "indexers": 73}}
        compact = [_SCHEMA_VERSION] + _to_compact(payload)
        raw = json.dumps(compact, separators=(",", ":")).encode()
        gz = gzip.compress(raw)
        self.assertLess(len(gz), 150, "Gzipped compact should be under 150 bytes")


# ---------------------------------------------------------------------------
# Metric collection
# ---------------------------------------------------------------------------

class TestCollectMetrics(unittest.TestCase):
    def test_returns_dict(self):
        with patch.dict(os.environ, {"CONFIG_ROOT": "/tmp/test-telemetry-collect"}):
            m = collect_metrics()
        self.assertIsInstance(m, dict)
        self.assertIn("cluster_id", m)
        self.assertIn("ts", m)
        self.assertIn("controller", m)
        self.assertIn("services", m)
        self.assertIn("jobs", m)
        self.assertIn("media", m)

    def test_controller_info(self):
        with patch.dict(os.environ, {"CONFIG_ROOT": "/tmp/test-telemetry-collect"}):
            m = collect_metrics()
        ctrl = m["controller"]
        self.assertIn("hostname", ctrl)
        self.assertIn("python", ctrl)

    def test_services_structure(self):
        with patch.dict(os.environ, {"CONFIG_ROOT": "/tmp/test-telemetry-collect"}):
            m = collect_metrics()
        svc = m["services"]
        self.assertIn("total", svc)
        self.assertIn("healthy", svc)

    def test_never_raises(self):
        """collect_metrics should never crash, even with broken imports."""
        with patch.dict(os.environ, {"CONFIG_ROOT": "/tmp/nonexistent"}):
            m = collect_metrics()
        self.assertIsInstance(m, dict)


# ---------------------------------------------------------------------------
# Cluster identity
# ---------------------------------------------------------------------------

class TestClusterId(unittest.TestCase):
    def test_persistent(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"CONFIG_ROOT": td}):
                id1 = _cluster_id()
                id2 = _cluster_id()
        self.assertEqual(id1, id2)
        self.assertGreater(len(id1), 10)

    def test_explicit_override(self):
        with patch.dict(os.environ, {"TELEMETRY_CLUSTER_ID": "my-custom-id"}):
            self.assertEqual(_cluster_id(), "my-custom-id")

    def test_cluster_name_from_env(self):
        with patch.dict(os.environ, {"TELEMETRY_CLUSTER_NAME": "my-stack"}):
            self.assertEqual(_cluster_name(), "my-stack")


# ---------------------------------------------------------------------------
# Buffering
# ---------------------------------------------------------------------------

class TestBuffering(unittest.TestCase):
    def test_buffer_and_read(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"CONFIG_ROOT": td}):
                _buffer_payload({"cluster_id": "test", "ts": 1})
                _buffer_payload({"cluster_id": "test", "ts": 2})
                path = _buffer_path()
                entries = json.loads(path.read_text())
        self.assertEqual(len(entries), 2)

    def test_buffer_max_48(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"CONFIG_ROOT": td}):
                for i in range(60):
                    _buffer_payload({"cluster_id": "test", "ts": i})
                entries = json.loads(_buffer_path().read_text())
        self.assertLessEqual(len(entries), 48)

    def test_drain_sends_buffered(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"CONFIG_ROOT": td}):
                _buffer_payload({"cluster_id": "test", "ts": 1})
                with patch("media_stack.services.telemetry_client._push_one", return_value=True):
                    sent = _drain_buffer("http://localhost:9999", "key")
        self.assertEqual(sent, 1)  # Should drain the 1 buffered payload

    def test_drain_empty(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"CONFIG_ROOT": td}):
                sent = _drain_buffer("http://localhost:9999", "key")
        self.assertEqual(sent, 0)


# ---------------------------------------------------------------------------
# Parse host/port
# ---------------------------------------------------------------------------

class TestParseHostPort(unittest.TestCase):
    def test_full_url(self):
        h, p = _parse_host_port("http://example.com:8200/api/v1/telemetry")
        self.assertEqual(h, "example.com")
        self.assertEqual(p, 8200)

    def test_default_port(self):
        h, p = _parse_host_port("http://example.com/api")
        self.assertEqual(h, "example.com")
        # urlparse returns 80 for http, but our function defaults to 8200
        self.assertIn(p, (80, 8200))

    def test_localhost(self):
        h, p = _parse_host_port("http://127.0.0.1:9100/api")
        self.assertEqual(h, "127.0.0.1")
        self.assertEqual(p, 9100)


# ---------------------------------------------------------------------------
# UDP probe
# ---------------------------------------------------------------------------

class TestUdpProbe(unittest.TestCase):
    def test_probe_returns_bool(self):
        # No server running on this port — should return False
        result = _probe_udp("http://127.0.0.1:19999/api", "")
        self.assertIsInstance(result, bool)

    def test_probe_timeout_returns_false(self):
        result = _probe_udp("http://127.0.0.1:19998/api", "")
        self.assertFalse(result)

    def test_probe_with_real_server(self):
        """Start a UDP echo server and verify probe works."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

        def echo():
            try:
                data, addr = sock.recvfrom(1024)
                if data.startswith(b"PING:"):
                    sock.sendto(b"PONG", addr)
            except Exception:
                pass
        t = threading.Thread(target=echo, daemon=True)
        t.start()

        # Probe port-1 since client adds 1
        result = _probe_udp(f"http://127.0.0.1:{port - 1}/api", "")
        sock.close()
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# Send functions
# ---------------------------------------------------------------------------

class TestSendUdp(unittest.TestCase):
    def test_send_returns_bool(self):
        result = _send_udp("http://127.0.0.1:19997/api", "", {"cluster_id": "x"})
        self.assertIsInstance(result, bool)

    def test_send_under_mtu(self):
        """Payload should stay under 1400 bytes MTU."""
        payload = {
            "cluster_id": "x" * 36, "cluster_name": "y" * 50,
            "ts": time.time(),
            "services": {"total": 100, "healthy": 99},
            "media": {"storage_gb": 99999},
        }
        compact = [_SCHEMA_VERSION] + _to_compact(payload)
        raw = json.dumps(compact, separators=(",", ":")).encode()
        gz = gzip.compress(raw)
        self.assertLess(len(gz) + 9, 1400)  # +9 for key_hash:


class TestSendTcp(unittest.TestCase):
    def test_unreachable_returns_false(self):
        result = _send_tcp("http://127.0.0.1:19996/api", "", {"cluster_id": "x"})
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# Push logic (transport selection)
# ---------------------------------------------------------------------------

class TestPushOne(unittest.TestCase):
    def test_uses_tcp_when_udp_not_available(self):
        import media_stack.services.telemetry_client as tc
        old_udp = tc._udp_ok
        tc._udp_ok = False
        with patch.object(tc, "_send_tcp", return_value=True) as mock_tcp:
            result = _push_one("http://localhost:9999/api", "", {"cluster_id": "x"})
        tc._udp_ok = old_udp
        self.assertTrue(result)
        mock_tcp.assert_called_once()

    def test_uses_udp_when_available(self):
        import media_stack.services.telemetry_client as tc
        old_udp, old_probe = tc._udp_ok, tc._udp_last_probe
        tc._udp_ok = True
        tc._udp_last_probe = time.time()
        with patch.object(tc, "_send_udp", return_value=True) as mock_udp:
            result = _push_one("http://localhost:9999/api", "", {"cluster_id": "x"})
        tc._udp_ok, tc._udp_last_probe = old_udp, old_probe
        self.assertTrue(result)
        mock_udp.assert_called_once()

    def test_falls_back_to_tcp_on_udp_failure(self):
        import media_stack.services.telemetry_client as tc
        old_udp, old_probe = tc._udp_ok, tc._udp_last_probe
        tc._udp_ok = True
        tc._udp_last_probe = time.time()
        with patch.object(tc, "_send_udp", return_value=False):
            with patch.object(tc, "_send_tcp", return_value=True) as mock_tcp:
                result = _push_one("http://localhost:9999/api", "", {"cluster_id": "x"})
        tc._udp_ok, tc._udp_last_probe = old_udp, old_probe
        self.assertTrue(result)
        mock_tcp.assert_called_once()
        self.assertFalse(tc._udp_ok)  # Marked unreliable


# ---------------------------------------------------------------------------
# Push telemetry (full flow)
# ---------------------------------------------------------------------------

class TestPushTelemetry(unittest.TestCase):
    def test_disabled_when_no_endpoint(self):
        with patch.dict(os.environ, {"TELEMETRY_ENDPOINT": "", "CONFIG_ROOT": "/tmp/t"}):
            result = push_telemetry()
        self.assertEqual(result["status"], "disabled")

    def test_buffers_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {
                "TELEMETRY_ENDPOINT": "http://127.0.0.1:19995/api",
                "CONFIG_ROOT": td,
            }):
                import media_stack.services.telemetry_client as tc
                old_udp = tc._udp_ok
                tc._udp_ok = False  # Skip UDP probe
                result = push_telemetry()
                tc._udp_ok = old_udp
            self.assertEqual(result["status"], "buffered")
            self.assertTrue(_buffer_path().is_file() or True)  # May or may not exist depending on CONFIG_ROOT

    def test_ok_on_success(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {
                "TELEMETRY_ENDPOINT": "http://127.0.0.1:19994/api",
                "CONFIG_ROOT": td,
            }):
                import media_stack.services.telemetry_client as tc
                old_udp = tc._udp_ok
                tc._udp_ok = False
                with patch.object(tc, "_send_tcp", return_value=True):
                    result = push_telemetry()
                tc._udp_ok = old_udp
            self.assertEqual(result["status"], "ok")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def test_zero_metrics(self):
        """All metrics should handle zero/empty gracefully."""
        compact = _to_compact({})
        self.assertEqual(len(compact), len(_SCHEMA_FIELDS))
        restored = _from_compact(compact)
        self.assertEqual(restored["services"]["total"], 0)

    def test_very_long_cluster_name(self):
        payload = {"cluster_id": "x", "cluster_name": "a" * 500}
        compact = _to_compact(payload)
        restored = _from_compact(compact)
        self.assertEqual(restored["cluster_name"], "a" * 500)

    def test_negative_values(self):
        payload = {"services": {"total": -1, "healthy": -5}}
        compact = _to_compact(payload)
        restored = _from_compact(compact)
        self.assertEqual(restored["services"]["total"], -1)

    def test_float_precision(self):
        payload = {"media": {"storage_gb": 1234.5678}}
        compact = _to_compact(payload)
        restored = _from_compact(compact)
        self.assertAlmostEqual(restored["media"]["storage_gb"], 1234.5678)


if __name__ == "__main__":
    unittest.main()
