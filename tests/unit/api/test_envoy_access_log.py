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


class TestKubectlTail(unittest.TestCase):
    def test_no_kubectl_returns_empty(self) -> None:
        with mock.patch.object(svc, "shutil") as m_shutil:
            m_shutil.which.return_value = None
            self.assertEqual(svc._kubectl_tail(10), [])

    def test_successful_tail_returns_lines(self) -> None:
        with mock.patch.object(svc, "shutil") as m_shutil, \
             mock.patch.object(svc, "subprocess") as m_subproc:
            m_shutil.which.return_value = "/usr/local/bin/kubectl"
            m_subproc.run.return_value = mock.Mock(
                returncode=0,
                stdout=b"line-a\nline-b\n",
            )
            self.assertEqual(svc._kubectl_tail(2), ["line-a", "line-b"])

    def test_failed_command_returns_empty(self) -> None:
        with mock.patch.object(svc, "shutil") as m_shutil, \
             mock.patch.object(svc, "subprocess") as m_subproc:
            m_shutil.which.return_value = "/usr/local/bin/kubectl"
            m_subproc.run.return_value = mock.Mock(
                returncode=1, stdout=b"",
            )
            self.assertEqual(svc._kubectl_tail(2), [])


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

    def test_falls_through_to_kubectl_when_in_k8s(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"KUBERNETES_SERVICE_HOST": "1.2.3.4"},
            clear=False,
        ), mock.patch.object(svc, "shutil") as m_shutil, \
           mock.patch.object(svc, "subprocess") as m_subproc:
            os.environ.pop("ENVOY_ACCESS_LOG_PATH", None)
            m_shutil.which.return_value = "/usr/local/bin/kubectl"
            m_subproc.run.return_value = mock.Mock(
                returncode=0, stdout=(SAMPLE_JSON + "\n").encode(),
            )
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
