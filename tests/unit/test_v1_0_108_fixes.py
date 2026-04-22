"""Ratchets for v1.0.108: HTTP retry log includes URL/method, and
discover-indexers scopes retries to 1 to avoid 35-min worst case.

Background: during fresh-install bootstrap, ``discover-indexers``
probes ~70 indexers (per-indexer Cardigann capability fetch).
Many time out (CloudFlare-protected, dead, slow). Pre-v1.0.108:

  - Each timeout retried 3 times = ~30s wasted per dead indexer.
    70 × 30s = 35 min worst case, longer than the 30-min bootstrap
    timeout.
  - The retry WARN logged ``retry operation=http.request
    attempt=N/3 ...`` with no URL — the operator couldn't tell
    which indexer was slow during a retry storm.

Fix:
  - HttpClient retry inlined into ``_execute_request_with_retry``,
    log includes method + URL.
  - Retry attempts read from env at call time (was module-import
    snapshot).
  - ``discover_indexers`` job adapter scopes attempts=1 for the
    duration so dead indexers cost ~10s instead of ~30s. Reputation
    system re-tries skipped indexers on later runs.
"""

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock as _mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


class HttpRetryLogIncludesUrl(unittest.TestCase):

    def test_retry_log_format_names_method_and_url(self) -> None:
        path = ROOT / "src/media_stack/core/http.py"
        text = path.read_text(encoding="utf-8")
        # The new log format includes ``%s %s`` for method+URL.
        self.assertIn(
            'log.warning(\n                    "retry %s %s attempt=',
            text,
            "HttpClient retry log no longer names method+URL — "
            "discover-indexers retry storms become unreadable.",
        )
        # Must NOT use the decorator's generic "operation=http.request"
        # form anywhere in HttpClient.
        self.assertNotIn(
            'operation="http.request"', text,
            "HttpClient still uses the generic-context @retry "
            "decorator — retry log will lose URL context.",
        )


class HttpRetryReadsAttemptsAtCallTime(unittest.TestCase):

    def test_attempts_env_var_evaluated_per_call(self) -> None:
        """Module-import-time env reads broke per-call overrides:
        a job that wanted 1 attempt couldn't change the value
        because HTTP_RETRY_ATTEMPTS was already a frozen module
        constant. Now reads ``MEDIA_STACK_HTTP_RETRY_ATTEMPTS``
        at request time."""
        path = ROOT / "src/media_stack/core/http.py"
        text = path.read_text(encoding="utf-8")
        # The retry helper must read MEDIA_STACK_HTTP_RETRY_ATTEMPTS
        # from os.environ inside the function body (not just at
        # module top).
        self.assertIn(
            'def _execute_request_with_retry', text,
        )
        retry_block_start = text.index('def _execute_request_with_retry')
        retry_block_end = text.index(
            'def _execute_request', retry_block_start + 1,
        )
        retry_body = text[retry_block_start:retry_block_end]
        self.assertIn(
            'os.environ.get(', retry_body,
            "_execute_request_with_retry must read env at call time "
            "so per-job overrides take effect.",
        )
        self.assertIn(
            'MEDIA_STACK_HTTP_RETRY_ATTEMPTS', retry_body,
        )


class DiscoverIndexersScopesRetriesToOne(unittest.TestCase):

    def test_adapter_sets_and_restores_env(self) -> None:
        path = ROOT / "src/media_stack/services/apps/core/job_adapters.py"
        text = path.read_text(encoding="utf-8")
        # Find the discover_indexers function body.
        idx = text.find("def discover_indexers(")
        self.assertGreater(idx, 0)
        # Take next ~1500 chars.
        body = text[idx:idx + 1500]
        # Must set MEDIA_STACK_HTTP_RETRY_ATTEMPTS to "1".
        self.assertIn('MEDIA_STACK_HTTP_RETRY_ATTEMPTS"] = "1"', body,
                      "discover_indexers must drop attempts to 1 "
                      "during the discovery scope.")
        # Must restore the previous value (try/finally pattern).
        self.assertIn("finally:", body)
        self.assertIn("if prev is None:", body)


class DiscoverIndexersRetryScopeIsLeakProof(unittest.TestCase):
    """Functional: verify the env var is restored even when the
    underlying action raises. Otherwise a failing
    discover-indexers leaks attempts=1 to every subsequent
    HTTP call in the same process."""

    def test_env_restored_on_success(self) -> None:
        os.environ.pop("MEDIA_STACK_HTTP_RETRY_ATTEMPTS", None)
        from media_stack.services.apps.core import job_adapters

        # Stub action_discover_indexers + _build_runner so the
        # adapter runs end-to-end without touching real services.
        with _mock.patch(
            "media_stack.cli.commands.action_handlers.action_discover_indexers"
        ), _mock.patch(
            "media_stack.cli.commands.controller_runner._build_runner"
        ):
            class _Ctx:
                config_root = "/srv-config"
                wait_timeout = 60
            # Spy on env DURING the call to make sure scoping happens.
            captured = {}

            def _spy(*a, **kw):
                captured["during"] = os.environ.get(
                    "MEDIA_STACK_HTTP_RETRY_ATTEMPTS"
                )

            import media_stack.cli.commands.action_handlers as _ah
            _ah.action_discover_indexers = _spy
            job_adapters.discover_indexers(_Ctx())

        self.assertEqual(captured.get("during"), "1",
                         "Env should be set to 1 inside the scope.")
        self.assertNotIn("MEDIA_STACK_HTTP_RETRY_ATTEMPTS", os.environ,
                         "Env should be unset after the scope "
                         "returns (was unset before).")

    def test_env_restored_on_exception(self) -> None:
        os.environ["MEDIA_STACK_HTTP_RETRY_ATTEMPTS"] = "5"
        from media_stack.services.apps.core import job_adapters

        with _mock.patch(
            "media_stack.cli.commands.action_handlers.action_discover_indexers"
        ), _mock.patch(
            "media_stack.cli.commands.controller_runner._build_runner"
        ):
            class _Ctx:
                config_root = "/srv-config"
                wait_timeout = 60

            def _boom(*a, **kw):
                raise RuntimeError("simulated failure")

            import media_stack.cli.commands.action_handlers as _ah
            _ah.action_discover_indexers = _boom
            with self.assertRaises(RuntimeError):
                job_adapters.discover_indexers(_Ctx())

        self.assertEqual(
            os.environ.get("MEDIA_STACK_HTTP_RETRY_ATTEMPTS"), "5",
            "Env should be restored to its prior value even when "
            "the wrapped action raises — otherwise leaks to every "
            "subsequent call in the same process.",
        )

        # Cleanup for other tests.
        os.environ.pop("MEDIA_STACK_HTTP_RETRY_ATTEMPTS", None)


if __name__ == "__main__":
    unittest.main()
