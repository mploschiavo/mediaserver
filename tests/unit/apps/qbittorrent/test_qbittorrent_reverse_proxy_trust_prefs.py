"""Both qBittorrent preflight code paths (k8s direct-API and compose
lifecycle-via-exec) must include the reverse-proxy trust settings in
their ``setPreferences`` payload.

Without these settings, Envoy-fronted requests that already passed
Authelia SSO still get challenged for qBittorrent's own WebUI login
— operators see a second prompt at
``apps.<domain>/app/qbittorrent/`` and the "one login" expectation
every other service satisfies is broken.

Before 2026-05-11 these settings only existed on operator-managed
clusters where someone had ticked Settings → Web UI → Authentication
→ "Bypass authentication for whitelisted IP subnets" through
qBittorrent's own WebUI; the values then survived in the PVC across
redeploys. This ratchet prevents that "manual config that nobody
wrote down" pattern from re-emerging by asserting both deploy-time
code paths set the prefs explicitly.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
import sys
sys.path.insert(0, str(ROOT / "src"))

from media_stack.infrastructure.qbittorrent import (  # noqa: E402
    QBITTORRENT_REVERSE_PROXY_TRUST_PREFS,
)

_EXPECTED_KEYS = frozenset({
    "bypass_local_auth",
    "bypass_auth_subnet_whitelist_enabled",
    "bypass_auth_subnet_whitelist",
    "web_ui_host_header_validation_enabled",
    "web_ui_csrf_protection_enabled",
})


class ReverseProxyTrustPrefsConstantTests(unittest.TestCase):
    """The constant itself — shape and values."""

    def test_constant_includes_every_expected_key(self) -> None:
        missing = _EXPECTED_KEYS - set(QBITTORRENT_REVERSE_PROXY_TRUST_PREFS)
        self.assertFalse(
            missing,
            f"QBITTORRENT_REVERSE_PROXY_TRUST_PREFS missing keys: {sorted(missing)}",
        )

    def test_bypass_subnet_whitelist_covers_rfc1918_plus_loopback(self) -> None:
        """The default qBittorrent auto-populates when an operator
        ticks the bypass option through the WebUI: three RFC1918 ranges
        + IPv4/IPv6 loopback. That set covers compose docker network
        (172.18.0.0/16 ⊂ 172.16.0.0/12), k3s pod CIDR (10.42.0.0/16 ⊂
        10.0.0.0/8), kubeadm-default (10.244.0.0/16 ⊂ 10.0.0.0/8), and
        typical LAN (192.168.x.x). Narrowing this would lock out
        legitimate operator tooling — see the constant's docstring."""
        whitelist = str(
            QBITTORRENT_REVERSE_PROXY_TRUST_PREFS["bypass_auth_subnet_whitelist"],
        )
        for required in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                         "127.0.0.1/32", "::1/128"):
            self.assertIn(required, whitelist)

    def test_bypass_flags_enabled(self) -> None:
        """The three boolean toggles that make qBittorrent actually
        honour the whitelist + skip its own WebUI auth on
        post-Authelia traffic."""
        self.assertIs(
            QBITTORRENT_REVERSE_PROXY_TRUST_PREFS["bypass_local_auth"], True,
        )
        self.assertIs(
            QBITTORRENT_REVERSE_PROXY_TRUST_PREFS["bypass_auth_subnet_whitelist_enabled"],
            True,
        )
        self.assertIs(
            QBITTORRENT_REVERSE_PROXY_TRUST_PREFS["web_ui_host_header_validation_enabled"],
            False,
        )
        self.assertIs(
            QBITTORRENT_REVERSE_PROXY_TRUST_PREFS["web_ui_csrf_protection_enabled"],
            False,
        )


class HttpPreflightWiringTests(unittest.TestCase):
    """The k8s-side ``http_preflight.run_preflight`` path must merge
    the trust prefs into the same ``setPreferences`` POST that sets
    credentials. Asserted by source-level inspection so the test
    doesn't have to spin up a real qBittorrent process or mock the
    HTTP plumbing — the wiring is what matters."""

    def test_http_preflight_imports_the_constant(self) -> None:
        src = (ROOT / "src" / "media_stack" / "infrastructure" /
               "qbittorrent" / "http_preflight.py").read_text(encoding="utf-8")
        self.assertIn(
            "QBITTORRENT_REVERSE_PROXY_TRUST_PREFS", src,
            "http_preflight.py must import the constant for SSO bypass",
        )

    def test_http_preflight_merges_prefs_into_setpreferences_call(self) -> None:
        src = (ROOT / "src" / "media_stack" / "infrastructure" /
               "qbittorrent" / "http_preflight.py").read_text(encoding="utf-8")
        # The ``run_preflight`` body must call ``prefs.update(...)``
        # with the constant — that's how the bypass settings reach
        # the API call. A ratchet against re-introducing the
        # "credentials only" payload that caused the 2026-05-11
        # compose SSO regression to surface.
        self.assertTrue(
            re.search(
                r"prefs\.update\(\s*QBITTORRENT_REVERSE_PROXY_TRUST_PREFS\s*\)",
                src,
            ),
            "http_preflight.run_preflight must merge "
            "QBITTORRENT_REVERSE_PROXY_TRUST_PREFS into its "
            "setPreferences payload",
        )


class LifecycleWiringTests(unittest.TestCase):
    """The compose-side ``QbittorrentLifecycle.ensure_credentials``
    path goes through ``_login_and_set_prefs_with_container_access``
    which builds ``prefs_json`` and exec_shells curl. Same invariant:
    the JSON body must include the bypass keys."""

    def test_lifecycle_imports_the_constant(self) -> None:
        src = (ROOT / "src" / "media_stack" / "adapters" /
               "qbittorrent" / "lifecycle.py").read_text(encoding="utf-8")
        self.assertIn(
            "QBITTORRENT_REVERSE_PROXY_TRUST_PREFS", src,
            "lifecycle.py must import the constant for SSO bypass",
        )

    def test_lifecycle_merges_prefs_into_setpreferences_call(self) -> None:
        src = (ROOT / "src" / "media_stack" / "adapters" /
               "qbittorrent" / "lifecycle.py").read_text(encoding="utf-8")
        self.assertTrue(
            re.search(
                r"prefs_payload\.update\(\s*QBITTORRENT_REVERSE_PROXY_TRUST_PREFS\s*\)",
                src,
            ),
            "QbittorrentLifecycle._login_and_set_prefs_with_container_access "
            "must merge QBITTORRENT_REVERSE_PROXY_TRUST_PREFS into prefs_payload",
        )


class JsonSerializationTests(unittest.TestCase):
    """Sanity check: the constant serializes to JSON the way
    qBittorrent's API expects (string values for the subnet list,
    bool values for the toggles)."""

    def test_constant_roundtrips_through_json(self) -> None:
        payload = {
            "web_ui_username": "admin",
            "web_ui_password": "secret",
            **QBITTORRENT_REVERSE_PROXY_TRUST_PREFS,
        }
        serialized = json.dumps(payload)
        decoded = json.loads(serialized)
        for key in _EXPECTED_KEYS:
            self.assertIn(key, decoded)
        self.assertIsInstance(
            decoded["bypass_auth_subnet_whitelist"], str,
            "subnet whitelist must be a comma-separated string",
        )


if __name__ == "__main__":
    unittest.main()
