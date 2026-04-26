"""Tests for the Envoy access-log tail service.

Three source modes (file / kubectl / docker compose) — each is
exercised independently with mocked subprocess + filesystem so the
test doesn't need a live Envoy.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services import envoy_access_log as svc  # noqa: E402


SAMPLE_JSON = (
    '{"ts": "2026-04-26T12:00:00Z", "method": "GET", '
    '"path": "/api/health", "status": 200, '
    '"upstream_cluster": "service_jellyfin", "duration": 12, '
    '"client_ip": "10.0.1.5"}'
)


class TestParseLine(unittest.TestCase):
    def test_parses_envoy_default_json(self) -> None:
        row = svc._parse_line(SAMPLE_JSON)
        self.assertEqual(row["method"], "GET")
        self.assertEqual(row["status"], 200)
        self.assertEqual(row["upstream"], "service_jellyfin")
        self.assertEqual(row["client_ip"], "10.0.1.5")

    def test_xff_chain_and_cf_connecting_ip_surface(self) -> None:
        # The Envoy template emits client_ip (resolved real IP after
        # the XFF trim), x_forwarded_for (full chain audit), and
        # cf_connecting_ip (Cloudflare's authoritative client IP).
        line = json.dumps({
            "ts": "2026-04-26T12:00:00Z",
            "method": "GET",
            "path": "/api/x",
            "status": 200,
            "client_ip": "98.57.21.136",
            "x_forwarded_for": "98.57.21.136, 10.0.1.5",
            "cf_connecting_ip": "98.57.21.136",
            "x_real_ip": "98.57.21.136",
            "host": "jf.iomio.io",
        })
        row = svc._parse_line(line)
        self.assertEqual(row["client_ip"], "98.57.21.136")
        self.assertEqual(
            row["x_forwarded_for"], "98.57.21.136, 10.0.1.5",
        )
        self.assertEqual(row["cf_connecting_ip"], "98.57.21.136")
        self.assertEqual(row["x_real_ip"], "98.57.21.136")
        self.assertEqual(row["host"], "jf.iomio.io")

    def test_alternate_keys_resolve(self) -> None:
        # Operators may set the access-log JSON keys differently;
        # accept the documented synonyms.
        line = json.dumps({
            "@timestamp": "2026-04-26T12:00:00Z",
            ":method": "POST",
            ":path": "/api/x",
            "response_code": 201,
            "cluster": "service_authelia",
            "duration_ms": 8,
        })
        row = svc._parse_line(line)
        self.assertEqual(row["method"], "POST")
        self.assertEqual(row["status"], 201)
        self.assertEqual(row["upstream"], "service_authelia")
        self.assertEqual(row["duration_ms"], 8)

    def test_non_json_returns_raw_only(self) -> None:
        row = svc._parse_line("not-a-json line")
        self.assertEqual(row, {"raw": "not-a-json line"})

    def test_empty_line_returns_empty_dict(self) -> None:
        self.assertEqual(svc._parse_line(""), {})

    def test_malformed_json_falls_back_to_raw(self) -> None:
        row = svc._parse_line('{"broken": ')
        self.assertEqual(row, {"raw": '{"broken":'})


class TestReadFile(unittest.TestCase):
    def test_returns_last_n_lines(self) -> None:
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as tf:
            for i in range(100):
                tf.write(f"line-{i}\n")
            path = Path(tf.name)
        try:
            lines = svc._read_file(path, limit=5)
            self.assertEqual(lines, [
                "line-95", "line-96", "line-97", "line-98", "line-99",
            ])
        finally:
            path.unlink()

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(
            svc._read_file(Path("/nonexistent/path"), limit=5),
            [],
        )


class TestK8sTail(unittest.TestCase):
    """The K8s code path uses the kubernetes Python client (lazy
    imported). Mock at the module level so tests don't need a live
    cluster."""

    def test_no_kubernetes_lib_returns_empty(self) -> None:
        # Simulate the import failing — the function should swallow
        # and return [].
        import builtins
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "kubernetes":
                raise ImportError("kubernetes not installed")
            return original_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", fake_import):
            self.assertEqual(svc._k8s_tail(10), [])

    def test_no_pods_returns_empty(self) -> None:
        # kubernetes is importable but the label resolves to zero pods.
        fake_kubernetes = mock.MagicMock()
        fake_v1 = mock.MagicMock()
        fake_v1.list_namespaced_pod.return_value = mock.Mock(items=[])
        fake_kubernetes.client.CoreV1Api.return_value = fake_v1
        fake_kubernetes.config.load_incluster_config = lambda: None
        with mock.patch.dict(
            "sys.modules",
            {
                "kubernetes": fake_kubernetes,
                "kubernetes.client": fake_kubernetes.client,
                "kubernetes.config": fake_kubernetes.config,
            },
        ):
            self.assertEqual(svc._k8s_tail(10), [])

    def test_successful_tail_returns_lines(self) -> None:
        fake_pod = mock.Mock(metadata=mock.Mock(name="envoy-abc"))
        fake_pod.metadata.name = "envoy-abc"
        fake_kubernetes = mock.MagicMock()
        fake_v1 = mock.MagicMock()
        fake_v1.list_namespaced_pod.return_value = mock.Mock(items=[fake_pod])
        fake_v1.read_namespaced_pod_log.return_value = "line-a\nline-b\n"
        fake_kubernetes.client.CoreV1Api.return_value = fake_v1
        fake_kubernetes.config.load_incluster_config = lambda: None
        with mock.patch.dict(
            "sys.modules",
            {
                "kubernetes": fake_kubernetes,
                "kubernetes.client": fake_kubernetes.client,
                "kubernetes.config": fake_kubernetes.config,
            },
        ):
            self.assertEqual(svc._k8s_tail(2), ["line-a", "line-b"])
            # Verify we passed the right tailLines argument.
            args, kwargs = fake_v1.read_namespaced_pod_log.call_args
            self.assertEqual(kwargs["tail_lines"], 2)
            self.assertEqual(kwargs["container"], "envoy")

    def test_api_exception_falls_through_silently(self) -> None:
        fake_kubernetes = mock.MagicMock()
        fake_v1 = mock.MagicMock()
        fake_v1.list_namespaced_pod.side_effect = RuntimeError("RBAC denied")
        fake_kubernetes.client.CoreV1Api.return_value = fake_v1
        fake_kubernetes.config.load_incluster_config = lambda: None
        with mock.patch.dict(
            "sys.modules",
            {
                "kubernetes": fake_kubernetes,
                "kubernetes.client": fake_kubernetes.client,
                "kubernetes.config": fake_kubernetes.config,
            },
        ):
            # Must not raise — fall through so the next source path
            # (docker compose) can try.
            self.assertEqual(svc._k8s_tail(2), [])


class TestDockerTail(unittest.TestCase):
    def test_no_docker_returns_empty(self) -> None:
        with mock.patch.object(svc, "shutil") as m_shutil:
            m_shutil.which.return_value = None
            self.assertEqual(svc._docker_tail(10), [])


class TestTailEnvoyAccessLog(unittest.TestCase):
    """End-to-end: ``tail_envoy_access_log`` picks the right source
    based on environment + parses every returned line."""

    def test_file_path_takes_precedence(self) -> None:
        # Build a tempfile, point the env var at it.
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as tf:
            tf.write(SAMPLE_JSON + "\n")
            tf.write("garbage line\n")
            path = tf.name
        try:
            with mock.patch.dict(os.environ, {"ENVOY_ACCESS_LOG_PATH": path}, clear=False):
                rows = svc.tail_envoy_access_log(limit=10)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["method"], "GET")
            self.assertEqual(rows[0]["status"], 200)
            self.assertEqual(rows[1]["raw"], "garbage line")
        finally:
            os.unlink(path)

    def test_falls_through_to_k8s_when_in_cluster(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"KUBERNETES_SERVICE_HOST": "1.2.3.4"},
            clear=False,
        ), mock.patch.object(svc, "_k8s_tail", return_value=[SAMPLE_JSON]):
            os.environ.pop("ENVOY_ACCESS_LOG_PATH", None)
            rows = svc.tail_envoy_access_log(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["method"], "GET")

    def test_limit_caps_returned_rows(self) -> None:
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as tf:
            for i in range(20):
                tf.write(json.dumps({
                    "method": "GET", "path": f"/{i}", "status": 200,
                }) + "\n")
            path = tf.name
        try:
            with mock.patch.dict(os.environ, {"ENVOY_ACCESS_LOG_PATH": path}, clear=False):
                rows = svc.tail_envoy_access_log(limit=5)
            self.assertEqual(len(rows), 5)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
