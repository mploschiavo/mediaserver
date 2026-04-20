"""Static guards for the services table's Auth column.

Before 2026-04-20 the column always rendered the native auth-probe
result (OK/Fail/No Key/N/A/error). Under Authelia/Authentik SSO the
ext_authz filter intercepts requests before the service sees them,
so the probe returned noise — most commonly ``error``, which
operators read as "auth is broken" even though SSO was working fine.

The column now branches on the active auth mode:
- Direct auth (``none`` / ``basic``): keep the native probe result
  so operators can still spot "the app's built-in auth is
  misconfigured".
- Gateway SSO (``authelia`` / ``authentik``): show the per-service
  policy (``Protected`` / ``Native`` / ``Public``) resolved from the
  contract + profile. That's what determines whether the user hits
  the Authelia portal before the app, which is the signal that
  actually matters under SSO.

These tests pin the static shape of the JS so the branching can't
regress silently — a code search for ``_authMode`` and ``SVC_POLICY``
asserts both exist and are used together in the render path.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "src" / "media_stack" / "api" / "dashboard.html"


class AuthColumnBranchTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.html = DASHBOARD.read_text(encoding="utf-8")

    def test_auth_mode_state_declared(self) -> None:
        """The active auth mode must be stored on a module-level
        variable the renderer can read. Without it, the column can't
        branch between probe-result and SSO-policy."""
        self.assertRegex(
            self.html, r"let\s+_authMode\s*=\s*['\"]none['\"]",
            "Expected `let _authMode='none'` — the variable the render "
            "path keys off to decide which column content to show.",
        )

    def test_policy_map_declared(self) -> None:
        """Per-service policy lookups must use a map. Without
        SVC_POLICY the SSO branch has no data to render."""
        self.assertIn(
            "SVC_POLICY", self.html,
            "Expected a `SVC_POLICY` map populated from "
            "/api/auth/service-policies — the SSO column reads it.",
        )

    def test_policy_loader_hits_correct_endpoints(self) -> None:
        """The fetch has to target the contract-backed endpoints —
        /api/auth/config for the active mode and
        /api/auth/service-policies for per-service policy. Hitting
        /api/auth/policies (typo) or inferring from /api/health
        would leave the map empty and the column blank."""
        self.assertIn("/api/auth/config", self.html)
        self.assertIn("/api/auth/service-policies", self.html)

    def test_sso_branch_renders_policy_labels(self) -> None:
        """The SSO branch must translate the three policy values
        into operator-facing labels. Missing any of the three means
        a category of services renders as blank under SSO."""
        for label in ("'SSO'", "'Native'", "'Public'"):
            self.assertIn(
                label, self.html,
                f"Expected label {label} in the SSO policy branch — "
                "without it, services with that policy render blank.",
            )

    def test_native_branch_still_shows_probe_result(self) -> None:
        """When auth mode is none/basic we must keep the original
        probe-result rendering. Ripping it out would regress the
        Basic-auth deploy path where the probe IS the useful signal."""
        # The original probe labels must still be present somewhere
        # in the renderer.
        for token in ("'OK'", "'Fail'", "'No Key'", "'N/A'"):
            self.assertIn(
                token, self.html,
                f"Expected native-auth probe label {token} still "
                "present — required for none/basic auth modes.",
            )

    def test_policy_map_loaded_on_boot(self) -> None:
        """loadAuthSummary has to be invoked during dashboard init,
        not only lazily when the Auth settings tab is opened — the
        services table on the Overview tab depends on it."""
        # The function must be defined and called outside its own
        # definition (so: at least 2 occurrences of the name).
        occurrences = len(re.findall(r"\bloadAuthSummary\b", self.html))
        self.assertGreaterEqual(
            occurrences, 2,
            "Expected loadAuthSummary defined AND invoked. Only one "
            "occurrence suggests it's defined but never called.",
        )


if __name__ == "__main__":
    unittest.main()
