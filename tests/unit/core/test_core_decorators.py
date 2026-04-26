import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.decorators import retry, timed  # noqa: E402


class RetryDecoratorTests(unittest.TestCase):
    def test_retry_retries_until_success(self):
        state = {"attempts": 0}

        @retry(attempts=3, delay_seconds=0, max_delay_seconds=0, operation="unit.retry")
        def flaky():
            state["attempts"] += 1
            if state["attempts"] < 3:
                raise RuntimeError("transient")
            return "ok"

        with mock.patch("time.sleep"):
            self.assertEqual(flaky(), "ok")
        self.assertEqual(state["attempts"], 3)

    def test_retry_honors_retry_predicate(self):
        state = {"attempts": 0}

        @retry(
            attempts=3,
            delay_seconds=0,
            max_delay_seconds=0,
            operation="unit.retry",
            retry_if=lambda exc: "retryable" in str(exc),
        )
        def non_retryable():
            state["attempts"] += 1
            raise RuntimeError("fatal")

        with mock.patch("time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "fatal"):
                non_retryable()
        self.assertEqual(state["attempts"], 1)


class TimedDecoratorTests(unittest.TestCase):
    def test_timed_logs_duration(self):
        logger = logging.getLogger("media_stack.test.timed")
        logger.setLevel(logging.DEBUG)
        with self.assertLogs("media_stack.test.timed", level="DEBUG") as ctx:

            @timed("unit.timed", logger=logger)
            def fn():
                return 42

            self.assertEqual(fn(), 42)

        output = "\n".join(ctx.output)
        self.assertIn("timing operation=unit.timed", output)


if __name__ == "__main__":
    unittest.main()
