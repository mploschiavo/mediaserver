"""Tests for AccessUrlDiscovery — the 'how do I reach this stack'
URL builder the dashboard uses when a user hasn't set DNS.

Contract highlights the tests pin:
- The request's Host-header IP is always first in the output so
  every URL the user sees is reachable from the browser they're
  already using.
- Loopback is present but last, so same-box dev still works.
- Gateway-style URLs (needs DNS) and direct-IP URLs (no DNS) are
  clearly labeled so the UI can show or hide them accordingly.
- The controller, Jellyfin, and gateway are the three services
  we promise URLs for. Others can be added without breaking the
  existing contract.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.access_urls import (  # noqa: E402
    AccessUrlDiscovery,
)


# Point every test at a fixed contract so the suite is deterministic
# regardless of the repo's contracts/access_urls.yaml content.
_FIXTURE_CONTRACT = """
version: 1
buckets:
  - slug: controller
    label: Controller
    direct_port: 9100
    scheme: http
  - slug: jellyfin
    label: Jellyfin
    direct_port: 8096
    scheme: http
    gateway_subdomain: jellyfin
    gateway_path: app/jellyfin
  - slug: gateway
    label: Gateway
    scheme: https
    apps_host_direct_ip: true
"""


def setUpModule():
    d = tempfile.mkdtemp(prefix="access_urls_fixture.")
    path = Path(d) / "access_urls.yaml"
    path.write_text(_FIXTURE_CONTRACT, encoding="utf-8")
    os.environ["ACCESS_URLS_CONTRACT"] = str(path)


def tearDownModule():
    os.environ.pop("ACCESS_URLS_CONTRACT", None)


class AccessUrlDiscoveryTests(unittest.TestCase):

    def test_host_hint_ip_is_first_reachable_url(self):
        """The IP the client already used to reach us is by
        definition reachable. Putting it first means the 'easy
        path' URL is always at the top of the UI."""
        out = AccessUrlDiscovery(host_ip_hint="192.168.1.60:9100").build()
        self.assertEqual(
            out["controller"][0]["url"],
            "http://192.168.1.60:9100/",
        )
        self.assertFalse(out["controller"][0]["needs_dns"])
        self.assertEqual(out["controller"][0]["kind"], "direct-ip")

    def test_strips_port_from_host_hint(self):
        """Host header includes the port; must be stripped before
        we rebuild URLs with our own per-service ports."""
        out = AccessUrlDiscovery(host_ip_hint="10.0.0.5:12345").build()
        self.assertIn("http://10.0.0.5:9100/",
                      [u["url"] for u in out["controller"]])
        self.assertIn("http://10.0.0.5:8096/",
                      [u["url"] for u in out["jellyfin"]])

    def test_loopback_appears_for_same_box_access(self):
        """A user running the dashboard on the same machine that
        hosts the stack should still see 127.0.0.1 in the list."""
        out = AccessUrlDiscovery(host_ip_hint="").build()
        urls = [u["url"] for u in out["controller"]]
        self.assertTrue(
            any("127.0.0.1" in u for u in urls),
            "127.0.0.1 must be present so same-box dev works "
            "without DNS / LAN-IP discovery.",
        )

    def test_non_ip_hostname_hint_falls_through_to_discovery(self):
        """If the user already has DNS set up and hits the
        controller via a hostname, we ignore the hint for direct-
        IP URLs (they'd be nonsense) and rely on LAN IP discovery
        + loopback."""
        out = AccessUrlDiscovery(host_ip_hint="apps.media-stack.local").build()
        urls = [u["url"] for u in out["controller"]]
        # No URL should be built with the hostname as the host
        self.assertFalse(
            any("apps.media-stack.local:9100" in u for u in urls),
            "Non-IP hints must not appear as direct-ip URLs — "
            "they'd require the same DNS the user is already using.",
        )

    def test_gateway_has_both_dns_and_direct_paths(self):
        """The gateway list MUST carry the DNS-required
        apps.media-stack.local entry AND the direct-IP fallback
        so the UI can surface both. Users with DNS click the
        nice one, users without click the IP one."""
        out = AccessUrlDiscovery(host_ip_hint="192.168.1.60").build()
        gateway_kinds = {u["kind"] for u in out["gateway"]}
        self.assertIn("direct-ip", gateway_kinds)
        self.assertIn("gateway", gateway_kinds)

    def test_jellyfin_has_direct_and_gateway_and_apps_paths(self):
        """Jellyfin surfaces three access patterns: its exposed
        host port (no DNS), the per-service virtual host
        (jellyfin.media-stack.local), and the path-prefix route
        on the apps host. All three are useful for different
        deployment styles."""
        out = AccessUrlDiscovery(host_ip_hint="192.168.1.60").build()
        kinds = {u["kind"] for u in out["jellyfin"]}
        self.assertIn("direct-ip", kinds)
        self.assertIn("gateway", kinds)
        self.assertIn("gateway-apps", kinds)

    def test_direct_ip_urls_are_never_flagged_needs_dns(self):
        """Direct-IP URLs MUST NOT carry needs_dns=True — that's
        exactly the whole point of the direct-IP kind."""
        out = AccessUrlDiscovery(host_ip_hint="192.168.1.60").build()
        for bucket in ("controller", "jellyfin", "gateway"):
            for u in out[bucket]:
                if u["kind"] == "direct-ip":
                    self.assertFalse(
                        u["needs_dns"],
                        f"{u['url']} is direct-ip but needs_dns=True",
                    )

    def test_gateway_kind_urls_always_flag_needs_dns(self):
        """Gateway-hostname URLs are useless without DNS / hosts
        entries. Flag accordingly so the UI can hide them for
        users who haven't set them up."""
        out = AccessUrlDiscovery(host_ip_hint="192.168.1.60").build()
        for bucket in ("controller", "jellyfin", "gateway"):
            for u in out[bucket]:
                if u["kind"] in ("gateway", "gateway-apps"):
                    self.assertTrue(
                        u["needs_dns"],
                        f"{u['url']} is a gateway URL but "
                        f"needs_dns=False",
                    )

    def test_empty_host_hint_falls_back_cleanly(self):
        """Bad or missing Host header must not break the response;
        loopback + whatever interfaces we can see must still be
        returned."""
        out = AccessUrlDiscovery(host_ip_hint="").build()
        self.assertGreater(len(out["controller"]), 0)

    def test_malformed_host_hint_is_safely_ignored(self):
        """Don't let a malformed Host header construct broken
        URLs. Discovery should still return loopback at minimum."""
        out = AccessUrlDiscovery(host_ip_hint=":::not:ipv4").build()
        urls = [u["url"] for u in out["controller"]]
        self.assertTrue(any("127.0.0.1" in u for u in urls))


if __name__ == "__main__":
    unittest.main()
