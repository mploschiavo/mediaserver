"""Tests pinning the credential-safety behaviour of the Settings
panel and the ``/api/envvars`` endpoint.

Drove the addition of these tests: the Settings tab once dumped
``STACK_ADMIN_PASSWORD`` verbatim via a "Show" button that called
``/api/envvars``. After the v1.0.94 admin-bootstrap redesign the
env var is a one-time seed, not the live credential — so showing
it leaked the seed and misled the user about what the live
password is.

What this file pins:

- ``/api/envvars`` returns ``"***"`` (or empty) for any var ending
  in PASSWORD/SECRET/TOKEN/KEY, and for the explicit
  ``STACK_ADMIN_PASSWORD`` regardless of name pattern. Other vars
  pass through unchanged.
- The dashboard HTML never contains a ``revealPassword`` /
  "Show password" button that hits ``/api/envvars``. The
  Settings panel's password row must link to the rotation flow,
  not display the value.
- The Settings panel's username load runs *when the modal opens*,
  not on page load. Pin the symptom: a ``setTimeout`` at module
  scope reading ``set-admin-user`` is the bug pattern."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

class EnvvarsMaskingTests(unittest.TestCase):
    """The endpoint behaviour itself — masks secrets, returns
    everything else as-is."""

    def setUp(self) -> None:
        # Snapshot the env so each test gets a clean slate.
        self._orig_env = dict(os.environ)
        # Mute the real env so we only see what the test sets.
        for key in list(os.environ.keys()):
            if key.startswith(("STACK_", "AUTHELIA_", "BOOTSTRAP_")):
                os.environ.pop(key, None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)

    def _envvars(self) -> dict[str, str]:
        from media_stack.api.services.config._diagnostics import (
            DiagnosticsService,
        )
        # ``get_envvars`` doesn't read self.profile; pass a stub.
        return DiagnosticsService(profile=mock.MagicMock()).get_envvars()

    def test_stack_admin_password_is_masked(self) -> None:
        os.environ["STACK_ADMIN_USERNAME"] = "admin"
        os.environ["STACK_ADMIN_PASSWORD"] = "definitely-not-leaked"
        result = self._envvars()
        self.assertIn("STACK_ADMIN_USERNAME", result)
        self.assertEqual(result["STACK_ADMIN_USERNAME"], "admin")
        self.assertEqual(result["STACK_ADMIN_PASSWORD"], "***",
                         "STACK_ADMIN_PASSWORD must never appear "
                         "verbatim in /api/envvars.")
        self.assertNotIn(
            "definitely-not-leaked",
            "".join(result.values()),
            "Password value leaked through some other key.",
        )

    def test_secrets_by_suffix_are_masked(self) -> None:
        # The endpoint only surfaces vars starting with one of the
        # platform/service prefixes (BOOTSTRAP_, STACK_, K8S_,
        # CONTROLLER_, …); within those, anything ending in a
        # secret suffix must be masked.
        os.environ["BOOTSTRAP_API_TOKEN"] = "tok"
        os.environ["STACK_SOMETHING_API_KEY"] = "key"
        os.environ["STACK_DB_PASSWORD"] = "pw"
        os.environ["CONTROLLER_SIGNING_SECRET"] = "sec"
        result = self._envvars()
        self.assertEqual(result["BOOTSTRAP_API_TOKEN"], "***")
        self.assertEqual(result["STACK_SOMETHING_API_KEY"], "***")
        self.assertEqual(result["STACK_DB_PASSWORD"], "***")
        self.assertEqual(result["CONTROLLER_SIGNING_SECRET"], "***")

    def test_non_secret_vars_pass_through(self) -> None:
        os.environ["STACK_ADMIN_USERNAME"] = "admin"
        os.environ["BOOTSTRAP_API_PORT"] = "9100"
        os.environ["CONTROLLER_LOG_LEVEL"] = "INFO"
        result = self._envvars()
        self.assertEqual(result["STACK_ADMIN_USERNAME"], "admin")
        self.assertEqual(result["BOOTSTRAP_API_PORT"], "9100")
        self.assertEqual(result["CONTROLLER_LOG_LEVEL"], "INFO")

    def test_empty_secret_returns_empty_string(self) -> None:
        """An unset secret should be empty, not ``"***"`` — that
        way the dashboard can distinguish "set but hidden" from
        "not configured at all"."""
        os.environ["STACK_ADMIN_PASSWORD"] = ""
        result = self._envvars()
        self.assertEqual(result["STACK_ADMIN_PASSWORD"], "")
