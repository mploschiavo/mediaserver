"""Unit tests for cli/workflows/cli_common.py."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.cli_common import (  # noqa: E402
    PhaseTracker,
    err,
    info,
    repo_root_from_script_file,
    run_command,
    ts,
    warn,
)
from media_stack.core.exceptions import MediaStackError  # noqa: E402


# ---------------------------------------------------------------------------
# ts() tests
# ---------------------------------------------------------------------------


class TestTs(unittest.TestCase):
    def test_returns_string(self):
        result = ts()
        self.assertIsInstance(result, str)

    def test_contains_date_separator(self):
        result = ts()
        self.assertIn("-", result)

    def test_contains_time_separator(self):
        result = ts()
        self.assertIn(":", result)

    def test_contains_t_separator(self):
        result = ts()
        self.assertIn("T", result)


# ---------------------------------------------------------------------------
# info() / warn() / err() tests
# ---------------------------------------------------------------------------


class TestLoggingHelpers(unittest.TestCase):
    def test_info_prints_to_stdout(self):
        with mock.patch("sys.stdout", new_callable=StringIO) as fake_out:
            info("hello world")
        self.assertIn("[INFO]", fake_out.getvalue())
        self.assertIn("hello world", fake_out.getvalue())

    def test_info_includes_timestamp(self):
        with mock.patch("sys.stdout", new_callable=StringIO) as fake_out:
            info("test")
        output = fake_out.getvalue()
        self.assertRegex(output, r"\d{4}-\d{2}-\d{2}T")

    def test_warn_prints_to_stderr(self):
        with mock.patch("sys.stderr", new_callable=StringIO) as fake_err:
            warn("caution")
        self.assertIn("[WARN]", fake_err.getvalue())
        self.assertIn("caution", fake_err.getvalue())

    def test_err_prints_to_stderr(self):
        with mock.patch("sys.stderr", new_callable=StringIO) as fake_err:
            err("bad stuff")
        self.assertIn("[ERR]", fake_err.getvalue())
        self.assertIn("bad stuff", fake_err.getvalue())

    def test_info_message_preserved(self):
        with mock.patch("sys.stdout", new_callable=StringIO) as fake_out:
            info("exact message check")
        self.assertIn("exact message check", fake_out.getvalue())

    def test_warn_message_preserved(self):
        with mock.patch("sys.stderr", new_callable=StringIO) as fake_err:
            warn("exact warning check")
        self.assertIn("exact warning check", fake_err.getvalue())

    def test_err_message_preserved(self):
        with mock.patch("sys.stderr", new_callable=StringIO) as fake_err:
            err("exact error check")
        self.assertIn("exact error check", fake_err.getvalue())


# ---------------------------------------------------------------------------
# PhaseTracker tests
# ---------------------------------------------------------------------------


class TestPhaseTracker(unittest.TestCase):
    def test_initial_state(self):
        pt = PhaseTracker()
        self.assertEqual(pt.current_phase, "")
        self.assertEqual(pt.current_start, 0)
        self.assertEqual(pt.names, [])
        self.assertEqual(pt.results, [])
        self.assertEqual(pt.seconds, [])

    def test_start_sets_current_phase(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.start("phase1")
        self.assertEqual(pt.current_phase, "phase1")

    def test_start_sets_current_start(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.start("phase1")
        self.assertGreater(pt.current_start, 0)

    def test_end_records_phase_name(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.start("my_phase")
            pt.end("ok")
        self.assertEqual(pt.names, ["my_phase"])

    def test_end_records_result(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.start("p1")
            pt.end("ok")
        self.assertEqual(pt.results, ["ok"])

    def test_end_records_elapsed_seconds(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.start("p1")
            pt.end("ok")
        self.assertEqual(len(pt.seconds), 1)
        self.assertIsInstance(pt.seconds[0], int)

    def test_end_resets_current_phase(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.start("p1")
            pt.end("ok")
        self.assertEqual(pt.current_phase, "")

    def test_end_resets_current_start(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.start("p1")
            pt.end("ok")
        self.assertEqual(pt.current_start, 0)

    def test_end_ok_logs_done(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO) as fake_out:
            pt.start("p1")
            pt.end("ok")
        self.assertIn("DONE", fake_out.getvalue())

    def test_end_skipped_logs_skip(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO) as fake_out:
            pt.start("p1")
            pt.end("skipped")
        self.assertIn("SKIP", fake_out.getvalue())

    def test_end_fail_logs_to_stderr(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.start("p1")
        with mock.patch("sys.stderr", new_callable=StringIO) as fake_err:
            pt.end("fail")
        self.assertIn("FAIL", fake_err.getvalue())

    def test_end_without_start_does_nothing(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.end("ok")
        self.assertEqual(pt.names, [])

    def test_multiple_phases(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.start("a")
            pt.end("ok")
            pt.start("b")
            pt.end("fail")
        self.assertEqual(pt.names, ["a", "b"])
        self.assertEqual(pt.results, ["ok", "fail"])

    def test_summary_no_phases(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO) as fake_out:
            pt.summary()
        self.assertIn("no phases recorded", fake_out.getvalue())

    def test_summary_with_phases(self):
        pt = PhaseTracker()
        with mock.patch("sys.stdout", new_callable=StringIO):
            pt.start("step1")
            pt.end("ok")
        with mock.patch("sys.stdout", new_callable=StringIO) as fake_out:
            pt.summary()
        output = fake_out.getvalue()
        self.assertIn("step1", output)
        self.assertIn("ok", output)

    def test_print_summary_alias(self):
        pt = PhaseTracker()
        self.assertEqual(pt.print_summary.__func__, pt.summary.__func__)

    def test_run_start_epoch_set(self):
        before = int(time.time())
        pt = PhaseTracker()
        after = int(time.time())
        self.assertGreaterEqual(pt.run_start_epoch, before)
        self.assertLessEqual(pt.run_start_epoch, after)


# ---------------------------------------------------------------------------
# run_command() tests
# ---------------------------------------------------------------------------


class TestRunCommand(unittest.TestCase):
    def test_successful_command(self):
        result = run_command(["echo", "hello"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)

    def test_raises_on_failure_by_default(self):
        with self.assertRaises(MediaStackError):
            run_command(["false"])

    def test_check_false_does_not_raise(self):
        result = run_command(["false"], check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_input_text_passed_to_stdin(self):
        result = run_command(["cat"], input_text="stdin_data")
        self.assertIn("stdin_data", result.stdout)

    def test_env_merged_with_os_environ(self):
        result = run_command(
            ["env"],
            env={"TEST_CLI_COMMON_VAR": "test_value_123"},
        )
        self.assertIn("TEST_CLI_COMMON_VAR=test_value_123", result.stdout)

    def test_captures_stderr(self):
        result = run_command(
            ["bash", "-c", "echo errout >&2"],
            check=False,
        )
        self.assertIn("errout", result.stderr)

    def test_error_message_includes_command(self):
        try:
            run_command(["bash", "-c", "echo fail_msg >&2; exit 1"])
        except MediaStackError as e:
            self.assertIn("fail_msg", str(e))
        else:
            self.fail("Expected MediaStackError")

    def test_error_message_fallback_to_exit_code(self):
        """When both stdout and stderr are empty, error references exit code."""
        try:
            run_command(["bash", "-c", "exit 42"])
        except MediaStackError as e:
            self.assertIn("42", str(e))
        else:
            self.fail("Expected MediaStackError")


# ---------------------------------------------------------------------------
# repo_root_from_script_file() tests
# ---------------------------------------------------------------------------


class TestRepoRootFromScriptFile(unittest.TestCase):
    def test_finds_repo_root_with_marker_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Create the marker structure
            contracts_dir = Path(tmp) / "contracts"
            contracts_dir.mkdir()
            (contracts_dir / "media-stack.config.json").write_text("{}")
            src_dir = Path(tmp) / "src" / "media_stack"
            src_dir.mkdir(parents=True)
            # Script file deep inside
            script = Path(tmp) / "src" / "media_stack" / "cli" / "workflows" / "my_script.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("# script")
            result = repo_root_from_script_file(str(script))
            self.assertEqual(result, Path(tmp))

    def test_fallback_when_no_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "a" / "b" / "c" / "d" / "e" / "f" / "script.py"
            script.parent.mkdir(parents=True)
            script.write_text("# script")
            result = repo_root_from_script_file(str(script))
            # With >= 5 parents, falls back to parents[4]
            self.assertIsInstance(result, Path)

    def test_shallow_path_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "script.py"
            script.write_text("# script")
            result = repo_root_from_script_file(str(script))
            # Fewer than 5 parents => falls back to resolved.parent
            self.assertEqual(result, script.resolve().parent)

    def test_resolves_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Create marker structure
            contracts_dir = Path(tmp) / "contracts"
            contracts_dir.mkdir()
            (contracts_dir / "media-stack.config.json").write_text("{}")
            src_dir = Path(tmp) / "src" / "media_stack"
            src_dir.mkdir(parents=True)
            real_script = Path(tmp) / "src" / "media_stack" / "real.py"
            real_script.write_text("# real")
            link_dir = Path(tmp) / "links"
            link_dir.mkdir()
            link = link_dir / "linked.py"
            try:
                link.symlink_to(real_script)
            except OSError:
                self.skipTest("Cannot create symlinks")
            result = repo_root_from_script_file(str(link))
            self.assertEqual(result, Path(tmp))


if __name__ == "__main__":
    unittest.main()
