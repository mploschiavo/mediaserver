"""Pre-deploy gate — every service that Envoy forwards with the
``/app/<slug>`` prefix intact MUST have a preflight handler that
sets its native config URL base to match. Without this, the app
serves its frontend from ``/`` and the browser 404s on every
asset — the 2026-04-19 Prowlarr blank-page bug.

Different from ``test_envoy_prefix_matches_app_url_base``:

- That one checks the live on-disk configs after a deploy: did
  someone remember to set UrlBase?
- This one checks the static ship-with-the-repo intent: if the
  service registry says "Envoy keeps the prefix", is there code
  that will set the matching UrlBase on any install?

Generalizes the today-specific
``test_every_arr_registers_servarr_http_preflight`` so new
path-prefix apps of any kind (not just *arrs) get caught at the
same gate.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

_CONTRACTS_DIR = ROOT / "contracts" / "services"

# Handlers the audit recognises as "this service sets its URL base
# somehow at boot." If a new family of apps needs a different
# handler, add its fully-qualified name here AND the test will
# verify that handler exists and is importable.
_URL_BASE_SETTING_HANDLERS = frozenset({
    # sonarr / radarr / lidarr / readarr / prowlarr — sets
    # <UrlBase>/app/<slug></UrlBase> in config.xml
    "media_stack.services.apps.servarr.http_preflight:run_preflight",
    # sabnzbd — sets url_base=/app/sabnzbd in sabnzbd.ini
    "media_stack.services.apps.sabnzbd.http_preflight:run_preflight",
})

# Services with preserve_path_prefix=True that legitimately need
# NO preflight because their frontend uses ``window.location``
# relative URLs (Jellyseerr, Homepage, etc.) — their HTML works
# under any prefix without an explicit UrlBase setting. Keep this
# list tight; when in doubt, add a preflight instead.
_RELATIVE_URL_APPS = frozenset({
    "jellyseerr",   # builds asset URLs from window.location.origin
    "homepage",     # static site, asset URLs are relative
    "maintainerr",  # relative asset URLs
    "flaresolverr", # API-only, no HTML UI
    "grabit",       # API-only
    "mythtv",       # not served via /app/
    "tautulli",     # relative asset URLs (verify if adding new)
    "unpackerr",    # no HTTP UI
    "bazarr",       # Python/Flask app, asset URLs are relative
                    # (verified live: /app/bazarr/ returns 200 with
                    # working assets even with no url_base set)
})


def _service_yaml_paths() -> list[Path]:
    return [p for p in sorted(_CONTRACTS_DIR.glob("*.yaml"))
            if not p.name.startswith("_")]


def _load_service(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class PathPrefixRoutesHaveUrlBasePreflightTests(unittest.TestCase):

    def test_preserve_path_prefix_services_have_url_base_preflight(self):
        """The invariant: if Envoy keeps the /app/<slug> prefix on
        forwarded requests, SOMETHING must configure the downstream
        app to serve from that prefix. Either a registered preflight
        OR an explicit 'this app uses relative URLs' allowlist
        entry. Anything that passes neither check is a blank-page
        bug waiting to happen."""
        offenders: list[str] = []
        for path in _service_yaml_paths():
            data = _load_service(path)
            svc = data.get("service") or {}
            if not svc.get("preserve_path_prefix"):
                continue
            slug = str(svc.get("id", ""))
            if slug in _RELATIVE_URL_APPS:
                continue
            # preflight_handler lives under the ``plugin:`` section
            # per the established contract shape (see jellyfin.yaml,
            # sabnzbd.yaml). Fall back to ``service:`` for contracts
            # that put it there — but the check is the same.
            plugin = data.get("plugin") or {}
            pf = plugin.get("preflight_handler") or svc.get("preflight_handler")
            handler = ""
            if isinstance(pf, dict):
                handler = str(pf.get("handler", "")).strip()
            if handler not in _URL_BASE_SETTING_HANDLERS:
                offenders.append(
                    f"{slug} (contract={path.name}): "
                    f"preserve_path_prefix=True but no recognised "
                    f"URL-base preflight registered "
                    f"(handler={handler or '<none>'!r})",
                )
        self.assertFalse(
            offenders,
            "Path-prefix services missing a UrlBase-setting "
            "preflight — Envoy forwards /app/<slug> to an app that "
            "will 404 on its own assets:\n  "
            + "\n  ".join(offenders)
            + "\n\nIf this app genuinely uses window.location / "
              "relative asset URLs and DOES NOT need a UrlBase, add "
              "its id to _RELATIVE_URL_APPS in this test with a "
              "one-line justification.",
        )

    def test_url_base_setting_handlers_exist_and_are_importable(self):
        """Every handler the audit recognises must actually exist.
        Catches a rename/delete that would silently turn this test
        into a no-op — pretty much the failure mode of the original
        ServarrHttpPreflight, which existed but nothing imported."""
        failed: list[str] = []
        for ref in _URL_BASE_SETTING_HANDLERS:
            module_name, _, attr = ref.partition(":")
            if not module_name or not attr:
                failed.append(f"{ref}: malformed (expected 'module:attr')")
                continue
            try:
                module = __import__(module_name, fromlist=[attr])
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{ref}: import failed ({exc})")
                continue
            if not hasattr(module, attr):
                failed.append(f"{ref}: attribute not found")
        self.assertFalse(failed, "\n".join(failed))


if __name__ == "__main__":
    unittest.main()
