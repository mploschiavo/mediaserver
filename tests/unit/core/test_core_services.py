"""Unit tests for core service modules with zero/low coverage.

Covers:
  - core/state_store.py (CheckpointStateStore)
  - core/exceptions.py (exception hierarchy)
  - core/http.py (HttpClient, helpers)
  - core/decorators.py (retry, timed)
  - core/phase_tracker.py (PhaseTracker)
  - core/subprocess_utils.py (CommandRunner, CommandResult)
  - core/filesystem.py (FileSystem)
  - core/logging_utils.py (configure_logging, log_event)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.state_store import CheckpointStateStore
from media_stack.core.exceptions import (
    CommandExecutionError,
    ConfigError,
    DockerError,
    KubernetesError,
    MediaStackError,
)
from media_stack.core.http import (
    HttpClient,
    RetryableHttpStatusError,
    _default_normalize_url,
    _is_retryable_http_error,
)
from media_stack.core.decorators import retry, timed
from media_stack.core.phase_tracker import PhaseTracker
from media_stack.core.subprocess_utils import CommandResult, CommandRunner
from media_stack.core.filesystem import FileSystem
from media_stack.core.logging_utils import configure_logging, log_event


# ---------------------------------------------------------------------------
# 1. CheckpointStateStore  (7 tests)
# ---------------------------------------------------------------------------


class TestCheckpointStateStoreLoad(unittest.TestCase):
    """Tests for CheckpointStateStore.load()."""

    def test_load_creates_phases_key_on_fresh_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CheckpointStateStore(Path(tmpdir) / "state.json")
            data = store.load()
            self.assertIsInstance(data.get("phases"), dict)
            self.assertEqual(data["schema"], 1)

    def test_load_reads_existing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text(json.dumps({"phases": {"setup": {"status": "ok"}}, "schema": 1}))
            store = CheckpointStateStore(path)
            data = store.load()
            self.assertEqual(data["phases"]["setup"]["status"], "ok")

    def test_load_handles_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text("NOT VALID JSON {{{")
            store = CheckpointStateStore(path)
            data = store.load()
            self.assertIsInstance(data.get("phases"), dict)


class TestCheckpointStateStoreSave(unittest.TestCase):
    """Tests for CheckpointStateStore.save()."""

    def test_save_writes_json_with_updated_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sub" / "state.json"
            store = CheckpointStateStore(path)
            store.load()
            store.save()
            saved = json.loads(path.read_text())
            self.assertIn("updated_at_epoch", saved)
            self.assertEqual(saved["schema"], 1)

    def test_clear_resets_data_and_saves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            store = CheckpointStateStore(path)
            store.load()
            store.mark_phase("init", "ok")
            store.clear()
            self.assertEqual(store.data["phases"], {})
            reloaded = json.loads(path.read_text())
            self.assertEqual(reloaded["phases"], {})


class TestCheckpointStateStorePhases(unittest.TestCase):
    """Tests for phase status helpers."""

    def test_phase_status_returns_empty_for_missing_phase(self):
        store = CheckpointStateStore(Path("/tmp/nonexistent_state.json"))
        store.load()
        self.assertEqual(store.phase_status("nonexistent"), "")

    def test_mark_phase_normalizes_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            store = CheckpointStateStore(path)
            store.load()
            store.mark_phase("deploy", "  OK  ")
            self.assertEqual(store.phase_status("deploy"), "ok")
            self.assertTrue(store.is_phase_done("deploy"))


# ---------------------------------------------------------------------------
# 2. Exceptions  (4 tests)
# ---------------------------------------------------------------------------


class TestExceptions(unittest.TestCase):
    """Tests for the custom exception hierarchy."""

    def test_media_stack_error_is_base_exception(self):
        err = MediaStackError("base error")
        self.assertIsInstance(err, Exception)
        self.assertEqual(str(err), "base error")

    def test_config_error_inherits_media_stack_error(self):
        err = ConfigError("bad config")
        self.assertIsInstance(err, MediaStackError)

    def test_command_execution_error_stores_details(self):
        err = CommandExecutionError("cmd failed", returncode=1, stdout="out", stderr="err")
        self.assertEqual(err.returncode, 1)
        self.assertEqual(err.stdout, "out")
        self.assertEqual(err.stderr, "err")
        self.assertIsInstance(err, MediaStackError)

    def test_kubernetes_and_docker_errors_inherit_base(self):
        self.assertIsInstance(KubernetesError("k8s"), MediaStackError)
        self.assertIsInstance(DockerError("docker"), MediaStackError)


# ---------------------------------------------------------------------------
# 3. HTTP utilities  (6 tests)
# ---------------------------------------------------------------------------


class TestRetryableHttpStatusError(unittest.TestCase):
    def test_stores_status_code_and_body(self):
        err = RetryableHttpStatusError(503, "service unavailable")
        self.assertEqual(err.status_code, 503)
        self.assertEqual(err.body, "service unavailable")
        self.assertIn("503", str(err))


class TestIsRetryableHttpError(unittest.TestCase):
    def test_url_error_is_retryable(self):
        from urllib import error as urlerror

        self.assertTrue(_is_retryable_http_error(urlerror.URLError("timeout")))

    def test_connection_error_is_retryable(self):
        self.assertTrue(_is_retryable_http_error(ConnectionError("refused")))

    def test_value_error_is_not_retryable(self):
        self.assertFalse(_is_retryable_http_error(ValueError("bad")))


class TestDefaultNormalizeUrl(unittest.TestCase):
    def test_strips_trailing_slash(self):
        self.assertEqual(_default_normalize_url("http://host:8080/"), "http://host:8080")

class TestHttpClientRequest(unittest.TestCase):
    @patch("media_stack.core.http.request.urlopen")
    def test_request_builds_correct_url_and_headers(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        client = HttpClient()
        status, body, raw = client.request(
            "http://localhost:8080/", "/api/test", api_key="secret123"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

        called_req = mock_urlopen.call_args[0][0]
        self.assertEqual(called_req.full_url, "http://localhost:8080/api/test")
        self.assertEqual(called_req.get_header("X-api-key"), "secret123")


# ---------------------------------------------------------------------------
# 4. Decorators  (6 tests)
# ---------------------------------------------------------------------------


class TestRetryDecorator(unittest.TestCase):
    def test_raises_on_invalid_attempts(self):
        with self.assertRaises(ValueError):
            retry(attempts=0)

    def test_raises_on_negative_delay(self):
        with self.assertRaises(ValueError):
            retry(delay_seconds=-1)

    @patch("time.sleep")
    def test_retry_respects_max_delay(self, mock_sleep):
        call_count = {"n": 0}

        @retry(
            attempts=4,
            delay_seconds=1.0,
            max_delay_seconds=2.0,
            backoff_multiplier=10.0,
            operation="test.max_delay",
        )
        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 4:
                raise RuntimeError("transient")
            return "done"

        result = flaky()
        self.assertEqual(result, "done")
        # After first retry sleep is 1.0, second should be capped at 2.0
        sleep_calls = [c[0][0] for c in mock_sleep.call_args_list]
        for s in sleep_calls:
            self.assertLessEqual(s, 2.0)

    def test_single_attempt_no_retry(self):
        @retry(attempts=1, delay_seconds=0, max_delay_seconds=0, operation="test.single")
        def always_fail():
            raise RuntimeError("fail")

        with self.assertRaises(RuntimeError):
            always_fail()


class TestTimedDecorator(unittest.TestCase):
    def test_timed_returns_function_result(self):
        @timed("test.op")
        def add(a, b):
            return a + b

        self.assertEqual(add(3, 4), 7)

    def test_timed_logs_even_on_exception(self):
        logger = logging.getLogger("media_stack.test.timed_exc")
        logger.setLevel(logging.DEBUG)

        @timed("test.exc", logger=logger)
        def boom():
            raise ValueError("kaboom")

        with self.assertLogs("media_stack.test.timed_exc", level="DEBUG"):
            with self.assertRaises(ValueError):
                boom()


# ---------------------------------------------------------------------------
# 5. PhaseTracker  (6 tests)
# ---------------------------------------------------------------------------


class TestPhaseTracker(unittest.TestCase):
    def _make_tracker(self):
        info_log = MagicMock()
        warn_log = MagicMock()
        tracker = PhaseTracker(info=info_log, warn=warn_log)
        return tracker, info_log, warn_log

    def test_start_records_phase_name(self):
        tracker, info_log, _ = self._make_tracker()
        tracker.start("deploy")
        self.assertEqual(tracker.current_phase, "deploy")
        info_log.assert_called_once()
        self.assertIn("deploy", info_log.call_args[0][0])

    def test_end_ok_records_result(self):
        tracker, info_log, _ = self._make_tracker()
        tracker.start("deploy")
        tracker.end("ok")
        self.assertEqual(tracker.names, ["deploy"])
        self.assertEqual(tracker.results, ["ok"])
        self.assertEqual(tracker.current_phase, "")

    def test_end_fail_uses_warn(self):
        tracker, _, warn_log = self._make_tracker()
        tracker.start("deploy")
        tracker.end("fail")
        warn_log.assert_called_once()
        self.assertIn("FAIL", warn_log.call_args[0][0])

    def test_end_skipped_uses_info(self):
        tracker, info_log, _ = self._make_tracker()
        tracker.start("optional")
        tracker.end("skipped")
        # start + skip = 2 info calls
        self.assertEqual(info_log.call_count, 2)
        self.assertIn("SKIP", info_log.call_args[0][0])

    def test_summary_with_no_phases(self):
        tracker, info_log, _ = self._make_tracker()
        tracker.summary()
        calls = [c[0][0] for c in info_log.call_args_list]
        self.assertTrue(any("no phases" in c for c in calls))

    def test_summary_lists_all_phases(self):
        tracker, info_log, _ = self._make_tracker()
        tracker.names = ["a", "b"]
        tracker.results = ["ok", "fail"]
        tracker.seconds = [1, 2]
        tracker.summary()
        summary_text = " ".join(c[0][0] for c in info_log.call_args_list)
        self.assertIn("a", summary_text)
        self.assertIn("b", summary_text)


# ---------------------------------------------------------------------------
# 6. CommandRunner / CommandResult  (5 tests)
# ---------------------------------------------------------------------------


class TestCommandResult(unittest.TestCase):
    def test_frozen_dataclass(self):
        result = CommandResult(args=["echo", "hi"], returncode=0, stdout="hi\n", stderr="")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "hi\n")
        with self.assertRaises(AttributeError):
            result.returncode = 1  # type: ignore[misc]


class TestCommandRunner(unittest.TestCase):
    @patch("media_stack.core.subprocess_utils.subprocess.run")
    def test_run_returns_command_result(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="output", stderr=""
        )
        runner = CommandRunner()
        result = runner.run(["echo", "hello"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "output")

    @patch("media_stack.core.subprocess_utils.subprocess.run")
    def test_run_raises_on_nonzero_when_check_true(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error msg"
        )
        runner = CommandRunner()
        with self.assertRaises(CommandExecutionError) as ctx:
            runner.run(["false"])
        self.assertEqual(ctx.exception.returncode, 1)
        self.assertEqual(ctx.exception.stderr, "error msg")

    @patch("media_stack.core.subprocess_utils.subprocess.run")
    def test_run_no_raise_when_check_false(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=42, stdout="", stderr="some error"
        )
        runner = CommandRunner()
        result = runner.run(["bad_cmd"], check=False)
        self.assertEqual(result.returncode, 42)

    @patch("media_stack.core.subprocess_utils.subprocess.run")
    def test_run_passes_env_and_timeout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = CommandRunner()
        runner.run(["ls"], env={"PATH": "/usr/bin"}, timeout=30)
        call_kwargs = mock_run.call_args
        self.assertEqual(call_kwargs.kwargs["env"], {"PATH": "/usr/bin"})
        self.assertEqual(call_kwargs.kwargs["timeout"], 30)


# ---------------------------------------------------------------------------
# 7. FileSystem  (3 tests)
# ---------------------------------------------------------------------------


class TestFileSystem(unittest.TestCase):
    def test_exists_returns_true_for_existing_file(self):
        with tempfile.NamedTemporaryFile() as f:
            fs = FileSystem()
            self.assertTrue(fs.exists(Path(f.name)))

    def test_read_and_write_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.txt"
            fs = FileSystem()
            fs.write_text(path, "hello world")
            self.assertEqual(fs.read_text(path), "hello world")

    def test_write_text_atomic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "atomic.txt"
            fs = FileSystem()
            fs.write_text_atomic(path, "atomic content")
            self.assertEqual(path.read_text(), "atomic content")
            # Verify temp file was cleaned up (replaced, not left behind)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            self.assertFalse(tmp_path.exists())


# ---------------------------------------------------------------------------
# 8. Logging utilities  (3 tests)
# ---------------------------------------------------------------------------


class TestConfigureLogging(unittest.TestCase):
    def test_returns_logger_with_handlers(self):
        # Use a unique logger name to avoid polluting other tests
        with patch("media_stack.core.logging_utils.logging.getLogger") as mock_get:
            logger = MagicMock()
            logger.handlers = []
            mock_get.return_value = logger
            result = configure_logging("DEBUG")
            self.assertEqual(result, logger)
            logger.setLevel.assert_called_once_with("DEBUG")
            logger.addHandler.assert_called_once()

    def test_idempotent_when_handlers_exist(self):
        with patch("media_stack.core.logging_utils.logging.getLogger") as mock_get:
            logger = MagicMock()
            logger.handlers = [MagicMock()]  # Already has handlers
            mock_get.return_value = logger
            result = configure_logging("INFO")
            self.assertEqual(result, logger)
            logger.setLevel.assert_not_called()


class TestLogEvent(unittest.TestCase):
    def test_emits_json_with_required_fields(self):
        logger = logging.getLogger("media_stack.test.log_event")
        logger.setLevel(logging.DEBUG)
        with self.assertLogs("media_stack.test.log_event", level="INFO") as ctx:
            log_event(logger, logging.INFO, "deploy.start", app="sonarr")
        output = ctx.output[0]
        # Format: "INFO:logger.name:{json}" — split on 2nd colon
        payload = json.loads(output.split(":", 2)[2].strip())
        self.assertEqual(payload["event"], "deploy.start")
        self.assertEqual(payload["app"], "sonarr")
        self.assertIn("ts", payload)
        self.assertEqual(payload["level"], "INFO")


if __name__ == "__main__":
    unittest.main()
