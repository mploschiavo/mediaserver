"""Ratchets for v1.0.111: usenet off by default + UI toggle.

Fresh installs without a usenet provider end up with SABnzbd
silently eating every grab while qBittorrent sits empty (the
*arr delay profile prefers usenet; SAB accepts the NZB; the
actual download fails on provider-auth; torrents never get a
chance). User sees "qBit has nothing to download" even though
everything looks configured.

Fix: ``download_clients.sabnzbd.configure_arr_clients`` defaults
to ``false``. Dashboard adds a "Usenet (SABnzbd)" toggle next to
Auto-Downloads. Flipping it on reconciles each *arr:

  - Sabnzbd download-client rows: enable=true
  - Delay profile preferredProtocol: usenet

Flipping it off:
  - Sabnzbd download-client rows: enable=false
  - Delay profile preferredProtocol: torrent (so qBit grabs fire
    immediately, no 0-delay-timeout wait-for-usenet)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


class SabnzbdOffByDefault(unittest.TestCase):

    def test_contract_default_is_false(self) -> None:
        import yaml
        text = (ROOT / "contracts/defaults/downloads.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        sab = (data.get("download_clients") or {}).get("sabnzbd") or {}
        self.assertFalse(
            bool(sab.get("configure_arr_clients", True)),
            "SABnzbd configure_arr_clients should default to false; "
            "with it true, fresh installs route every grab to a "
            "usenet client that doesn't have provider credentials "
            "and qBittorrent never sees a request.",
        )


class PatchArrUsenetEnabledReconciler(unittest.TestCase):

    def setUp(self) -> None:
        from media_stack.services.apps.servarr import arr_runtime_defaults
        self.mod = arr_runtime_defaults

    def _run(self, *, usenet_enabled, initial_dc_enable, initial_proto):
        put_calls = []
        dc = {"id": 1, "implementation": "Sabnzbd",
              "enable": initial_dc_enable, "name": "SABnzbd"}
        dp = {"id": 1, "preferredProtocol": initial_proto,
              "usenetDelay": 0, "torrentDelay": 0}

        def http(base, path, *, api_key="", method="GET",
                 payload=None, timeout=15):
            if path == "/api/v3/downloadclient" and method == "GET":
                return 200, [dc], b""
            if path == "/api/v3/delayprofile" and method == "GET":
                return 200, [dp], b""
            if method == "PUT":
                put_calls.append((path, payload))
                return 202, {}, b""
            return 404, None, b""

        captured = []
        self.mod.patch_arr_usenet_enabled(
            arr_url="http://sonarr:8989",
            api_ver="v3",
            api_key="K",
            usenet_enabled=usenet_enabled,
            http_request=http,
            log=captured.append,
        )
        return put_calls, captured

    def test_disabling_usenet_flips_sab_client_and_delay_profile(self) -> None:
        puts, logs = self._run(
            usenet_enabled=False,
            initial_dc_enable=True,
            initial_proto="usenet",
        )
        # Must have updated both the SAB client and the delay profile.
        dc_put = next((p for p in puts if "/downloadclient/" in p[0]), None)
        dp_put = next((p for p in puts if "/delayprofile/" in p[0]), None)
        self.assertIsNotNone(dc_put, "SAB client not updated")
        self.assertIsNotNone(dp_put, "Delay profile not updated")
        self.assertFalse(dc_put[1]["enable"],
                         "SAB client should be disabled")
        self.assertEqual(dp_put[1]["preferredProtocol"], "torrent",
                         "Delay profile should prefer torrent when usenet off")

    def test_enabling_usenet_restores_sab_and_switches_back_to_usenet(self) -> None:
        puts, _ = self._run(
            usenet_enabled=True,
            initial_dc_enable=False,
            initial_proto="torrent",
        )
        dc_put = next((p for p in puts if "/downloadclient/" in p[0]), None)
        dp_put = next((p for p in puts if "/delayprofile/" in p[0]), None)
        self.assertIsNotNone(dc_put)
        self.assertIsNotNone(dp_put)
        self.assertTrue(dc_put[1]["enable"])
        self.assertEqual(dp_put[1]["preferredProtocol"], "usenet")

    def test_idempotent_when_already_in_desired_state(self) -> None:
        puts, _ = self._run(
            usenet_enabled=False,
            initial_dc_enable=False,
            initial_proto="torrent",
        )
        # Already off + already torrent → no PUTs.
        self.assertEqual(puts, [])


class DashboardToggle(unittest.TestCase):

    def test_ui_has_usenet_toggle(self) -> None:
        html = (ROOT / "src/media_stack/api/dashboard.html").read_text(encoding="utf-8")
        self.assertIn('id="usenetToggle"', html,
                      "usenetToggle checkbox missing from dashboard")
        self.assertIn("async function toggleUsenet(", html,
                      "toggleUsenet handler missing")
        # Toggle POSTs download_clients.sabnzbd.configure_arr_clients.
        self.assertIn(
            "download_clients:{sabnzbd:{configure_arr_clients:on}}", html,
            "toggleUsenet POST body doesn't flip the right cfg key.",
        )

    def test_load_wires_toggle_state(self) -> None:
        html = (ROOT / "src/media_stack/api/dashboard.html").read_text(encoding="utf-8")
        # load() must read the runtime config and update the toggle.
        self.assertIn(
            "sabCfg.configure_arr_clients", html,
            "load() doesn't read SABnzbd toggle state from cfg",
        )


class PublishedPortForAsymmetricServices(unittest.TestCase):
    """Services where the host-published port differs from the
    container's internal port. SABnzbd is the only default case:
    internal 8080 (published 8085, because qBittorrent already
    owns 8080 on the host). Direct-URL browsers need the 8085 port
    or the link 404s."""

    def test_servicedef_has_published_port_field(self) -> None:
        from media_stack.api.services.registry import ServiceDef
        sd = ServiceDef(id="x", name="x", port=8080, published_port=8085)
        self.assertEqual(sd.published_port, 8085)
        # Defaults to 0 when unset — callers use ``port`` as fallback.
        sd2 = ServiceDef(id="y", name="y", port=8989)
        self.assertEqual(sd2.published_port, 0)

    def test_yaml_loader_passes_published_port_through(self) -> None:
        """v1.0.113 incident: ServiceDef had the field, the
        contract had the value, the API surface coerced it — but
        the YAML→ServiceDef parser silently dropped it because
        ``_parse_service_entry`` didn't read the field. Result:
        live ``/api/services`` returned ``published_port == port``
        for every service. SAB direct link still went to :8080."""
        from media_stack.api.services.registry import _parse_service_entry
        sd = _parse_service_entry({
            "id": "sabnzbd", "name": "SABnzbd",
            "host": "sabnzbd", "port": 8080,
            "published_port": 8085,
        })
        self.assertEqual(sd.published_port, 8085,
                         "published_port from YAML must reach "
                         "ServiceDef — otherwise the dashboard "
                         "direct link reverts to the container port.")
        # When YAML omits it, ServiceDef.published_port is 0 — the
        # API-surface coercion fills in port at serialize time.
        sd2 = _parse_service_entry({
            "id": "sonarr", "name": "Sonarr",
            "host": "sonarr", "port": 8989,
        })
        self.assertEqual(sd2.published_port, 0)

    def test_sabnzbd_contract_sets_published_port_8085(self) -> None:
        import yaml
        text = (ROOT / "contracts/services/sabnzbd.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        svc = data.get("service") or {}
        self.assertEqual(svc.get("port"), 8080,
                         "SABnzbd's in-container port must stay 8080")
        self.assertEqual(svc.get("published_port"), 8085,
                         "SABnzbd's host-side publication port must "
                         "be 8085 — anything else breaks the direct "
                         "link from the dashboard.")

    def test_services_api_surfaces_published_port(self) -> None:
        text = (ROOT / "src/media_stack/api/handlers_get.py").read_text(encoding="utf-8")
        self.assertIn(
            '"published_port":', text,
            "/api/services no longer surfaces published_port — "
            "dashboard falls back to port and builds broken SAB links.",
        )

    def test_dashboard_prefers_published_port_in_direct_url(self) -> None:
        html = (ROOT / "src/media_stack/api/dashboard.html").read_text(encoding="utf-8")
        # getSvcUrl must consult published_port with fallback to port.
        self.assertIn(
            "s.published_port||s.port", html,
            "getSvcUrl no longer prefers published_port — "
            "SABnzbd's ribbon/services-table link reverts to :8080.",
        )


class ServiceDefFieldsAreAllParsed(unittest.TestCase):
    """Generic ratchet for the v1.0.113 "published_port silently
    dropped" bug class.

    The pattern: ``ServiceDef`` is a 30+ field dataclass. The YAML
    loader ``_parse_service_entry`` reads each field by name. If
    you add a field to the dataclass and forget the parser line,
    the field defaults silently — no error, no warning, just
    wrong behaviour at runtime.

    This test introspects ``ServiceDef`` at runtime and asserts
    EVERY public field is mentioned in ``_parse_service_entry``.
    Adds a hard wall against the same bug class. Same pattern
    can be applied to other dataclass+parser pairs as they grow."""

    def test_every_servicedef_field_is_read_by_parser(self) -> None:
        import dataclasses, re
        from media_stack.api.services import registry
        src = Path(registry.__file__).read_text(encoding="utf-8")
        m = re.search(
            r"def _parse_service_entry\(.*?return ServiceDef\(.*?\)\s*\n",
            src, re.DOTALL,
        )
        self.assertIsNotNone(
            m, "Couldn't locate _parse_service_entry — refactor "
               "broke this ratchet's grep.",
        )
        parser_body = m.group(0)
        missing = []
        for f in dataclasses.fields(registry.ServiceDef):
            if f.name.startswith("_"):
                continue
            # Field is "read" if its name appears as a dict-get key
            # OR as a kwarg name in the ServiceDef(...) call.
            patterns = (
                f'"{f.name}"', f"'{f.name}'", f"{f.name}=",
            )
            if not any(p in parser_body for p in patterns):
                missing.append(f.name)
        self.assertFalse(
            missing,
            f"ServiceDef fields not parsed from YAML: {missing}.\n"
            "Each field added to ServiceDef must also be read in "
            "_parse_service_entry. Without this, the YAML value is "
            "silently dropped and callers see the dataclass default "
            "at runtime (the v1.0.113 published_port=8080-instead-of-8085 "
            "incident).",
        )


if __name__ == "__main__":
    unittest.main()
