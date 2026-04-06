import io
import signal
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.workflows.unit_test_runner_service import (  # noqa: E402
    UnitTestRunnerConfig,
    run_unit_test_suite,
)


class UnitTestRunnerServiceTests(unittest.TestCase):
    def test_run_unit_test_suite_emits_telemetry_summary_for_success(self):
        class PassingTest(unittest.TestCase):
            def runTest(self):
                self.assertTrue(True)

        suite = unittest.TestSuite([PassingTest()])
        cfg = UnitTestRunnerConfig(root_dir=ROOT, top_n=1, verbosity=0)
        stream = io.StringIO()

        exit_code, records = run_unit_test_suite(suite=suite, cfg=cfg, stream=stream)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "ok")
        output = stream.getvalue()
        self.assertIn("[TEST-RESOURCE] status=ok", output)
        self.assertIn("[TEST-RESOURCE] slowest_tests", output)
        self.assertIn("[TEST-RESOURCE] highest_memory_tests", output)

    def test_run_unit_test_suite_marks_failed_tests(self):
        class FailingTest(unittest.TestCase):
            def runTest(self):
                self.fail("intentional test failure")

        suite = unittest.TestSuite([FailingTest()])
        cfg = UnitTestRunnerConfig(root_dir=ROOT, top_n=1, verbosity=0)
        stream = io.StringIO()

        exit_code, records = run_unit_test_suite(suite=suite, cfg=cfg, stream=stream)

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "fail")

    def test_run_unit_test_suite_marks_timed_out_tests(self):
        if not hasattr(signal, "SIGALRM"):
            self.skipTest("SIGALRM is not available on this platform")

        class SlowTest(unittest.TestCase):
            def runTest(self):
                time.sleep(0.2)

        suite = unittest.TestSuite([SlowTest()])
        cfg = UnitTestRunnerConfig(root_dir=ROOT, top_n=1, verbosity=0, timeout_seconds=0.05)
        stream = io.StringIO()

        exit_code, records = run_unit_test_suite(suite=suite, cfg=cfg, stream=stream)

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "timeout")
        self.assertIn("status=timeout", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
