"""Regression tests for Envoy ext_authz path_prefix construction.

This is the code that sits between "user clicks POST reset-password
in the dashboard" and "Authelia decides allow/deny". It has broken
twice in production:

  1. Envoy's path_prefix appends the original request URI to the
     configured prefix. An earlier config put `rd=<portal>` in the
     prefix directly; Envoy then produced
     `/api/verify?rd=<portal><original-path>` which corrupted `rd`
     and broke the login redirect.

  2. Switching to `/api/authz/forward-auth` fixed (1) but introduced
     a worse bug: that endpoint rejects non-safe methods (POST, PUT,
     DELETE) with 405 text/plain, which Envoy propagates verbatim to
     the browser. Dashboard POSTs (reset-password, delete-user, etc.)
     saw `"405 Method Not Allowed"` as the response body, the UI's
     `fetch().json()` choked, and the user got a cryptic syntax error.

The current design uses `/api/verify?authz_path=` (POST-safe) with
`rd=<portal>&` injected BEFORE `authz_path=` at render time. The
tests below lock in every edge case so a future config tweak can't
reintroduce either bug silently.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.envoy_ext_authz import (  # noqa: E402
    _prefix_with_rd,
    build_ext_authz_filter,
)
from media_stack.core.auth.gateway_policy import ExtAuthzConfig  # noqa: E402


class PrefixWithRdTests(unittest.TestCase):
    """_prefix_with_rd injects the portal URL into the ext_authz
    path_prefix when the contract uses the authz_path form."""

    def test_injects_rd_before_authz_path(self):
        """The canonical compose setup: Authelia /api/verify with the
        authz_path query param last so Envoy's path-append lands
        somewhere harmless. rd= must be URL-encoded and placed BEFORE
        authz_path so the appended request URI can't corrupt it."""
        out = _prefix_with_rd(
            "/api/verify?authz_path=",
            "https://auth.media-stack.local/",
        )
        self.assertEqual(
            out,
            "/api/verify?rd=https%3A%2F%2Fauth.media-stack.local%2F&authz_path=",
        )

    def test_authz_path_stays_last(self):
        """authz_path MUST be the last query param so that when Envoy
        appends the request URI verbatim, it lands inside authz_path
        (harmless) instead of after rd= (corrupting the portal URL)."""
        out = _prefix_with_rd(
            "/api/verify?authz_path=",
            "https://auth.example.local/",
        )
        self.assertTrue(out.endswith("&authz_path="),
                        f"authz_path must be trailing; got {out!r}")

    def test_empty_portal_url_leaves_prefix_unchanged(self):
        """Without a portal URL, Authelia can't emit a 302 with
        Location. We leave the prefix alone rather than inject an
        empty rd= that breaks browser redirects."""
        self.assertEqual(
            _prefix_with_rd("/api/verify?authz_path=", ""),
            "/api/verify?authz_path=",
        )

    def test_non_authz_prefix_pass_through(self):
        """Authentik's outpost path is not an authz_path-style endpoint —
        it handles redirect itself. Injecting rd= would break it."""
        self.assertEqual(
            _prefix_with_rd(
                "/outpost.goauthentik.io/auth/envoy",
                "https://auth.example.local/",
            ),
            "/outpost.goauthentik.io/auth/envoy",
        )

    def test_existing_rd_not_double_injected(self):
        """If the profile already specifies rd= (override), honor it
        verbatim. Don't prepend a second rd= that Authelia would see
        as the first-wins value."""
        self.assertEqual(
            _prefix_with_rd(
                "/api/verify?rd=https%3A%2F%2Fcustom%2F&authz_path=",
                "https://default/",
            ),
            "/api/verify?rd=https%3A%2F%2Fcustom%2F&authz_path=",
        )


class BuildExtAuthzFilterTests(unittest.TestCase):
    """The filter body handed to Envoy must use the rd-injected
    prefix. A regression here is what made POST password-reset fail."""

    def test_filter_uses_post_friendly_endpoint(self):
        """Authelia /api/verify accepts every HTTP method. The filter
        must target it — not /api/authz/forward-auth, which 405s on
        POST and blew up the dashboard."""
        cfg = ExtAuthzConfig(
            cluster_name="ext_authz_authelia",
            host="authelia",
            port=9091,
            path_prefix="/api/verify?authz_path=",
            response_headers_to_add=("Remote-User",),
        )
        built = build_ext_authz_filter(cfg, "https://auth.media-stack.local/")
        rendered_prefix = (
            built["typed_config"]["http_service"]["path_prefix"]
        )
        self.assertIn("/api/verify", rendered_prefix,
                      "ext_authz must hit /api/verify (POST-safe); "
                      "/api/authz/forward-auth returns 405 on POST.")
        self.assertNotIn("/api/authz/forward-auth", rendered_prefix,
                         "forward-auth is GET-only and breaks dashboard "
                         "POSTs — never target it from compose.")

    def test_filter_keeps_failure_mode_allow_false(self):
        """fail-closed: a broken auth service must deny, not allow.
        If this flips to True, an Authelia outage becomes silent auth
        bypass — worst possible failure mode for an internet-exposed
        deployment."""
        cfg = ExtAuthzConfig(
            cluster_name="ext_authz_authelia",
            host="authelia", port=9091,
            path_prefix="/api/verify?authz_path=",
            response_headers_to_add=("Remote-User",),
        )
        built = build_ext_authz_filter(cfg, "https://auth.local/")
        self.assertFalse(built["typed_config"]["failure_mode_allow"])

    def test_filter_forwards_remote_user_upstream(self):
        """Without Remote-User forwarding to upstream, the controller's
        trusted-proxy auth falls back to Basic and users see a second
        password prompt after a successful Authelia login."""
        cfg = ExtAuthzConfig(
            cluster_name="ext_authz_authelia",
            host="authelia", port=9091,
            path_prefix="/api/verify?authz_path=",
            response_headers_to_add=("Remote-User", "Remote-Groups"),
        )
        built = build_ext_authz_filter(cfg, "https://auth.local/")
        allowed = (built["typed_config"]["http_service"]
                   ["authorization_response"]["allowed_upstream_headers"]
                   ["patterns"])
        names = [p["exact"] for p in allowed]
        self.assertIn("Remote-User", names)


if __name__ == "__main__":
    unittest.main()
