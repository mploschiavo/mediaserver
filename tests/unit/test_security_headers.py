"""Tests for the centralised security-header policy.

Every header the STRICT and LEGACY policies emit is pinned to an
expected value. A change to a preset is deliberate — if it breaks
a test, someone is weakening the policy and the reviewer should
know.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.security_headers import (  # noqa: E402
    CSPPolicy,
    DEFAULT_POLICY,
    LEGACY_DASHBOARD_POLICY,
    STRICT_POLICY,
    SecurityHeaders,
    apply_policy,
    merged_headers,
)


class CSPPolicyRenderTests(unittest.TestCase):

    def test_default_renders_self_everywhere(self) -> None:
        out = CSPPolicy().render()
        self.assertIn("default-src 'self'", out)
        self.assertIn("script-src 'self'", out)
        self.assertIn("style-src 'self'", out)
        self.assertIn("frame-ancestors 'none'", out)
        self.assertIn("object-src 'none'", out)
        self.assertIn("base-uri 'self'", out)
        self.assertIn("form-action 'self'", out)

    def test_empty_directive_omitted(self) -> None:
        # require-trusted-types-for is empty by default — should not
        # appear at all.
        out = CSPPolicy().render()
        self.assertNotIn("require-trusted-types-for", out)

    def test_require_trusted_types_rendered_when_set(self) -> None:
        out = CSPPolicy(require_trusted_types_for=("'script'",)).render()
        self.assertIn("require-trusted-types-for 'script'", out)

    def test_img_data_uri_permitted_by_default(self) -> None:
        # Data-URIs for small inline images are ubiquitous; allow by
        # default but document the call.
        out = CSPPolicy().render()
        self.assertIn("img-src 'self' data:", out)

    def test_multiple_values_space_separated(self) -> None:
        out = CSPPolicy(script_src=("'self'", "'unsafe-inline'")).render()
        self.assertIn("script-src 'self' 'unsafe-inline'", out)

    def test_directives_semicolon_joined(self) -> None:
        out = CSPPolicy().render()
        # No trailing semicolon (matches browser tolerance + is the
        # prevailing convention on MDN examples).
        self.assertFalse(out.endswith(";"))
        self.assertIn("; ", out)


class SecurityHeadersAsDictTests(unittest.TestCase):

    def test_default_contains_required_headers(self) -> None:
        h = SecurityHeaders().as_header_dict()
        for name in (
            "Content-Security-Policy",
            "Strict-Transport-Security",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
            "Cross-Origin-Opener-Policy",
            "Cross-Origin-Resource-Policy",
            "Cache-Control",
            "Server",
        ):
            self.assertIn(name, h, f"missing header: {name}")

    def test_empty_field_omitted(self) -> None:
        h = SecurityHeaders(hsts="").as_header_dict()
        self.assertNotIn("Strict-Transport-Security", h)

    def test_strip_server_banner_false_omits_server(self) -> None:
        h = SecurityHeaders(strip_server_banner=False).as_header_dict()
        self.assertNotIn("Server", h)

    def test_xcto_is_nosniff(self) -> None:
        self.assertEqual(
            SecurityHeaders().as_header_dict()["X-Content-Type-Options"],
            "nosniff",
        )

    def test_x_frame_options_is_deny(self) -> None:
        self.assertEqual(
            SecurityHeaders().as_header_dict()["X-Frame-Options"],
            "DENY",
        )

    def test_cache_control_no_store(self) -> None:
        # Sensitive auth-gated responses must not hit browser cache.
        self.assertIn(
            "no-store",
            SecurityHeaders().as_header_dict()["Cache-Control"],
        )

    def test_permissions_policy_disables_device_apis(self) -> None:
        pp = SecurityHeaders().as_header_dict()["Permissions-Policy"]
        for api in (
            "geolocation", "camera", "microphone",
            "payment", "usb", "interest-cohort",
        ):
            self.assertIn(f"{api}=()", pp)

    def test_coop_same_origin(self) -> None:
        self.assertEqual(
            SecurityHeaders().as_header_dict()[
                "Cross-Origin-Opener-Policy"],
            "same-origin",
        )

    def test_hsts_includes_subdomains(self) -> None:
        self.assertIn(
            "includeSubDomains",
            SecurityHeaders().as_header_dict()[
                "Strict-Transport-Security"],
        )


class ApplyTests(unittest.TestCase):

    def test_apply_calls_send_header_for_each(self) -> None:
        handler = MagicMock()
        SecurityHeaders().apply(handler)
        expected = set(SecurityHeaders().as_header_dict().keys())
        actual = {
            call.args[0] for call in handler.send_header.call_args_list
        }
        self.assertEqual(actual, expected)

    def test_apply_policy_convenience_uses_default(self) -> None:
        handler = MagicMock()
        apply_policy(handler)
        # default policy includes at least HSTS.
        names = {
            call.args[0] for call in handler.send_header.call_args_list
        }
        self.assertIn("Strict-Transport-Security", names)

    def test_apply_policy_accepts_override(self) -> None:
        handler = MagicMock()
        apply_policy(handler, STRICT_POLICY)
        csp_calls = [
            call for call in handler.send_header.call_args_list
            if call.args[0] == "Content-Security-Policy"
        ]
        self.assertEqual(len(csp_calls), 1)
        # STRICT_POLICY adds Trusted-Types.
        self.assertIn(
            "require-trusted-types-for 'script'", csp_calls[0].args[1],
        )


class PresetDifferentiationTests(unittest.TestCase):
    """Presets must be meaningfully different — STRICT is stricter."""

    def test_strict_forbids_unsafe_inline_scripts(self) -> None:
        csp = STRICT_POLICY.as_header_dict()["Content-Security-Policy"]
        # script-src does NOT contain unsafe-inline.
        script_part = [
            p for p in csp.split("; ") if p.startswith("script-src ")
        ][0]
        self.assertNotIn("'unsafe-inline'", script_part)

    def test_legacy_permits_unsafe_inline_scripts(self) -> None:
        # Until the dashboard's inline JS is extracted, the legacy
        # preset allows unsafe-inline scripts. If this ever FLIPS we
        # want to notice.
        csp = LEGACY_DASHBOARD_POLICY.as_header_dict()[
            "Content-Security-Policy"]
        script_part = [
            p for p in csp.split("; ") if p.startswith("script-src ")
        ][0]
        self.assertIn("'unsafe-inline'", script_part)

    def test_strict_has_trusted_types(self) -> None:
        csp = STRICT_POLICY.as_header_dict()["Content-Security-Policy"]
        self.assertIn("require-trusted-types-for 'script'", csp)

    def test_legacy_does_not_have_trusted_types(self) -> None:
        # Trusted Types on the legacy dashboard would break every
        # the inline DOM sinks the legacy dashboard used. Legacy preset omits it.
        csp = LEGACY_DASHBOARD_POLICY.as_header_dict()[
            "Content-Security-Policy"]
        self.assertNotIn("require-trusted-types-for", csp)

    def test_strict_has_coep(self) -> None:
        self.assertEqual(
            STRICT_POLICY.as_header_dict()["Cross-Origin-Embedder-Policy"],
            "require-corp",
        )

    def test_legacy_omits_coep(self) -> None:
        # COEP on the big dashboard breaks external image loads.
        self.assertNotIn(
            "Cross-Origin-Embedder-Policy",
            LEGACY_DASHBOARD_POLICY.as_header_dict(),
        )


class WithOverridesTests(unittest.TestCase):

    def test_returns_new_instance(self) -> None:
        a = SecurityHeaders()
        b = a.with_overrides(hsts="max-age=60")
        self.assertIsNot(a, b)
        self.assertEqual(
            a.as_header_dict()["Strict-Transport-Security"],
            "max-age=31536000; includeSubDomains",
        )
        self.assertEqual(
            b.as_header_dict()["Strict-Transport-Security"], "max-age=60",
        )

    def test_original_is_immutable(self) -> None:
        a = SecurityHeaders()
        with self.assertRaises(Exception):
            a.hsts = "max-age=0"  # type: ignore[misc]


class MergedHeadersTests(unittest.TestCase):

    def test_none_overrides_returns_policy_dict(self) -> None:
        self.assertEqual(
            merged_headers(STRICT_POLICY),
            STRICT_POLICY.as_header_dict(),
        )

    def test_overrides_replace_existing_keys(self) -> None:
        out = merged_headers(
            STRICT_POLICY, {"Cache-Control": "max-age=3600"},
        )
        self.assertEqual(out["Cache-Control"], "max-age=3600")

    def test_overrides_add_new_keys(self) -> None:
        out = merged_headers(
            STRICT_POLICY, {"Content-Type": "application/json"},
        )
        self.assertEqual(out["Content-Type"], "application/json")


class DefaultPolicyTests(unittest.TestCase):

    def test_default_is_legacy_during_migration(self) -> None:
        # During the inline-dashboard migration, the default MUST
        # be the legacy preset — switching to STRICT would break
        # the existing dashboard for every admin. This assertion
        # flags the day we flip.
        self.assertIs(DEFAULT_POLICY, LEGACY_DASHBOARD_POLICY)


if __name__ == "__main__":
    unittest.main()
