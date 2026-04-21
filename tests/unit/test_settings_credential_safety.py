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

DASHBOARD_HTML = (
    ROOT / "src" / "media_stack" / "api" / "dashboard.html"
).read_text(encoding="utf-8")


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


class DashboardSettingsPanelTests(unittest.TestCase):
    """The HTML/JS side. Pattern-match on ``dashboard.html`` to
    catch the credential-leak regression at the source."""

    def test_no_reveal_password_function(self) -> None:
        """The old ``revealPassword(btn)`` function dumped
        STACK_ADMIN_PASSWORD into the DOM. It must not come
        back."""
        self.assertNotIn(
            "function revealPassword",
            DASHBOARD_HTML,
            "revealPassword() leaks STACK_ADMIN_PASSWORD — the "
            "Settings panel must use a rotation link instead.",
        )

    def test_no_show_password_button(self) -> None:
        """The old "Show" button on the password row called
        revealPassword. Pin the absence so a copy-paste regression
        fails this test."""
        self.assertNotIn(
            "onclick=\"revealPassword(this)\"",
            DASHBOARD_HTML,
            "A 'Show' button on the password row leaks the seed "
            "credential.",
        )

    def test_settings_password_row_links_to_change_flow(self) -> None:
        """Positive signal: the password row should mention
        "Change password" so the user has a path forward."""
        self.assertIn(
            "Change password",
            DASHBOARD_HTML,
            "Settings panel must offer a Change-password action "
            "in place of the old Show button.",
        )

    def test_settings_username_load_runs_on_modal_open(self) -> None:
        """Pin the bug shape: the symptom of "Loading..." stuck
        forever was a ``setTimeout(...,100)`` at module scope that
        ran 100ms after page load — the modal didn't exist yet, so
        the function silently returned. The fix is a function
        called from inside ``showSettings()`` after innerHTML is
        set."""
        self.assertIn(
            "loadSettingsAdminUsername()",
            DASHBOARD_HTML,
            "Expected a callable that loads the username when the "
            "modal opens, not a fire-once setTimeout at module "
            "scope.",
        )
        # And that function uses /api/auth/identity (the
        # authenticated user) before falling back to /api/keys —
        # not /api/envvars (env var, may be stale after rotation).
        idx = DASHBOARD_HTML.find("function loadSettingsAdminUsername")
        self.assertGreater(idx, -1)
        body = DASHBOARD_HTML[idx:idx + 800]
        self.assertIn("/api/auth/identity", body)
        self.assertNotIn(
            "/api/envvars", body,
            "Username must come from the authenticated session, "
            "not the env var (which may be the pre-rotation seed).",
        )


if __name__ == "__main__":
    unittest.main()
