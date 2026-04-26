"""Ratchet: every canonical preset in ``core.auth.security_headers``
must emit the mandatory hardening headers.

If a preset ever silently drops one of these headers — because a
future refactor "simplifies" the module — CI fails here. The test is
definition-level: it does NOT require every HTTP handler to call
``apply_policy`` (that coverage lives in integration tests). Instead
it pins the shape of the presets so a drift is loud at unit-test
time.

The list of mandatory headers encodes current defense-in-depth:
browser cache protection, MIME sniffing guard, clickjacking guard,
referrer leak control, device-API disable, cross-origin isolation,
and HSTS.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.security_headers import (  # noqa: E402
    DEFAULT_POLICY,
    LEGACY_DASHBOARD_POLICY,
    STRICT_POLICY,
    SecurityHeaders,
)


_MANDATORY_HEADERS: tuple[str, ...] = (
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Resource-Policy",
    "Cache-Control",
)

_MANDATORY_CSP_DIRECTIVES: tuple[str, ...] = (
    "default-src",
    "frame-ancestors",
    "base-uri",
    "form-action",
    "object-src",
)


class MandatoryHeaderRatchet(unittest.TestCase):

    def _check(self, policy: SecurityHeaders, label: str) -> None:
        emitted = policy.as_header_dict()
        for name in _MANDATORY_HEADERS:
            self.assertIn(
                name, emitted,
                f"{label} policy is missing mandatory header: {name}",
            )

    def test_strict_policy_has_all_mandatory_headers(self) -> None:
        self._check(STRICT_POLICY, "STRICT")

    def test_legacy_policy_has_all_mandatory_headers(self) -> None:
        self._check(LEGACY_DASHBOARD_POLICY, "LEGACY_DASHBOARD")

    def test_default_policy_has_all_mandatory_headers(self) -> None:
        self._check(DEFAULT_POLICY, "DEFAULT")


class MandatoryCSPDirectivesRatchet(unittest.TestCase):

    def _csp(self, policy: SecurityHeaders) -> str:
        return policy.as_header_dict()["Content-Security-Policy"]

    def _check(self, policy: SecurityHeaders, label: str) -> None:
        csp = self._csp(policy)
        for directive in _MANDATORY_CSP_DIRECTIVES:
            self.assertIn(
                f"{directive} ", csp,
                f"{label} CSP is missing directive: {directive}",
            )

    def test_strict_csp_directives(self) -> None:
        self._check(STRICT_POLICY, "STRICT")

    def test_legacy_csp_directives(self) -> None:
        self._check(LEGACY_DASHBOARD_POLICY, "LEGACY_DASHBOARD")


class NeverWeakenedRatchet(unittest.TestCase):
    """Per-value assertions that catch specific regressions — a new
    preset that silently allows `frame-ancestors *` or
    `X-Frame-Options: ALLOWALL` would slip past a generic presence
    check but fail here."""

    def test_frame_ancestors_never_opens_up(self) -> None:
        for policy in (STRICT_POLICY, LEGACY_DASHBOARD_POLICY):
            csp = policy.as_header_dict()["Content-Security-Policy"]
            self.assertNotIn(
                "frame-ancestors *", csp,
                "frame-ancestors must NEVER be '*' — clickjacking guard",
            )
            self.assertIn("frame-ancestors 'none'", csp)

    def test_object_src_never_opens_up(self) -> None:
        for policy in (STRICT_POLICY, LEGACY_DASHBOARD_POLICY):
            csp = policy.as_header_dict()["Content-Security-Policy"]
            self.assertIn("object-src 'none'", csp)

    def test_x_frame_options_never_anything_but_deny(self) -> None:
        for policy in (STRICT_POLICY, LEGACY_DASHBOARD_POLICY):
            self.assertEqual(
                policy.as_header_dict()["X-Frame-Options"], "DENY",
            )

    def test_x_content_type_options_is_nosniff(self) -> None:
        for policy in (STRICT_POLICY, LEGACY_DASHBOARD_POLICY):
            self.assertEqual(
                policy.as_header_dict()["X-Content-Type-Options"],
                "nosniff",
            )

    def test_hsts_at_least_one_year(self) -> None:
        min_age = 31536000  # 1 year
        for policy in (STRICT_POLICY, LEGACY_DASHBOARD_POLICY):
            hsts = policy.as_header_dict()["Strict-Transport-Security"]
            # Parse out "max-age=N" — naive but sufficient.
            for token in hsts.split(";"):
                token = token.strip()
                if token.startswith("max-age="):
                    try:
                        age = int(token.split("=", 1)[1])
                    except ValueError:
                        self.fail(f"unparseable max-age in HSTS: {hsts}")
                    self.assertGreaterEqual(
                        age, min_age,
                        f"HSTS max-age must be >= 1 year, got {age}",
                    )
                    break
            else:
                self.fail(f"HSTS header has no max-age: {hsts}")

    def test_cache_control_includes_no_store(self) -> None:
        for policy in (STRICT_POLICY, LEGACY_DASHBOARD_POLICY):
            cc = policy.as_header_dict()["Cache-Control"]
            self.assertIn(
                "no-store", cc,
                "auth-gated responses must not land in browser cache",
            )

    def test_permissions_policy_disables_device_apis(self) -> None:
        for policy in (STRICT_POLICY, LEGACY_DASHBOARD_POLICY):
            pp = policy.as_header_dict()["Permissions-Policy"]
            for api in ("geolocation", "camera", "microphone", "payment"):
                self.assertIn(
                    f"{api}=()", pp,
                    f"Permissions-Policy must disable {api}",
                )


class StrictSurpassesLegacyRatchet(unittest.TestCase):
    """STRICT must be demonstrably stricter than LEGACY — else one of
    them is a no-op and someone should notice."""

    def test_strict_csp_is_shorter_or_equal_on_script_src(self) -> None:
        strict_csp = STRICT_POLICY.as_header_dict()[
            "Content-Security-Policy"]
        legacy_csp = LEGACY_DASHBOARD_POLICY.as_header_dict()[
            "Content-Security-Policy"]
        strict_script = [
            p for p in strict_csp.split("; ") if p.startswith("script-src ")
        ][0]
        legacy_script = [
            p for p in legacy_csp.split("; ") if p.startswith("script-src ")
        ][0]
        # A stricter policy has FEWER or equal terms in script-src.
        self.assertLessEqual(
            len(strict_script.split()), len(legacy_script.split()),
            "STRICT.script-src must have <= terms as LEGACY",
        )

    def test_strict_has_trusted_types_legacy_does_not(self) -> None:
        strict_csp = STRICT_POLICY.as_header_dict()[
            "Content-Security-Policy"]
        legacy_csp = LEGACY_DASHBOARD_POLICY.as_header_dict()[
            "Content-Security-Policy"]
        self.assertIn("require-trusted-types-for", strict_csp)
        self.assertNotIn("require-trusted-types-for", legacy_csp)


if __name__ == "__main__":
    unittest.main()
