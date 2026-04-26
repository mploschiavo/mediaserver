"""Static consistency: every ``/app/<slug>`` prefix Envoy routes
must match a ``<UrlBase>/app/<slug></UrlBase>`` in the app's config.

Motivation — 2026-04-19 Prowlarr blank-page incident: Envoy
forwarded ``/app/prowlarr/...`` to the container without stripping
the prefix, but Prowlarr's ``config.xml`` had ``<UrlBase></UrlBase>``
(empty). The app served pages from ``/`` and every asset reference
in the HTML resolved to a path Envoy didn't route to Prowlarr, so
the browser loaded the HTML and then 404'd on every ``<script>``
and ``<link>`` — blank page.

There was no unit test that cross-checked these two files against
each other, so the mismatch shipped.

This test does three things:

1. Parse the LIVE ``docker/config/envoy/envoy.yaml`` — if present —
   and extract every ``prefix: /app/<slug>`` route.
2. For each matched app, parse the on-disk ``config.xml`` and read
   its ``<UrlBase>`` element.
3. Assert the UrlBase equals ``/app/<slug>``.

Apps not (yet) deployed are skipped — this is a post-deploy audit,
not a pre-deploy gate. A companion test
(``test_all_path_prefix_routes_have_url_base_preflight``) is the
pre-deploy gate: every route in the generator MUST have a preflight
handler that will set the matching UrlBase on first boot.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
_ENVOY_YAML = ROOT / "docker" / "config" / "envoy" / "envoy.yaml"
_CONFIGS_ROOT = ROOT / "docker" / "config"

# Services with config.xml + <UrlBase>. Adding a new *arr-like app
# = add it here. Non-*arr apps either use a different config file
# format (handled separately) or have no UrlBase concept.
_CONFIG_XML_APPS = ("sonarr", "radarr", "lidarr", "readarr", "prowlarr")

_PREFIX_RE = re.compile(r"prefix:\s*(/app/[a-z0-9][a-z0-9-]*)\b")
_URLBASE_RE = re.compile(r"<UrlBase>([^<]*)</UrlBase>")


def _envoy_app_prefixes() -> set[str]:
    """Return the set of /app/<slug> prefixes Envoy routes — as a
    set of slugs (without the /app/ prefix) so the check against
    per-app config files is a straight lookup."""
    if not _ENVOY_YAML.is_file():
        return set()
    text = _ENVOY_YAML.read_text(encoding="utf-8")
    slugs: set[str] = set()
    for m in _PREFIX_RE.finditer(text):
        full = m.group(1)  # "/app/<slug>"
        slug = full.rsplit("/", 1)[-1]
        slugs.add(slug)
    return slugs


class EnvoyPrefixMatchesAppUrlBaseTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        if not _ENVOY_YAML.is_file():
            raise unittest.SkipTest(
                "docker/config/envoy/envoy.yaml not present — "
                "test only runs against a deployed compose stack "
                "(the generator produces it on first boot).",
            )

    def test_every_envoy_app_prefix_has_matching_url_base(self):
        """The contract: if Envoy advertises ``/app/<slug>`` as a
        route, the app's on-disk config must be serving from that
        same prefix. Mismatch = blank page in the browser."""
        mismatches: list[str] = []
        absent_configs: list[str] = []

        for slug in _envoy_app_prefixes():
            if slug not in _CONFIG_XML_APPS:
                # Apps without a <UrlBase>-style config are covered
                # by their own routing tests; this file is scoped
                # to the *arr family.
                continue
            config_path = _CONFIGS_ROOT / slug / "config.xml"
            if not config_path.is_file():
                absent_configs.append(slug)
                continue
            text = config_path.read_text(encoding="utf-8")
            match = _URLBASE_RE.search(text)
            actual = match.group(1).strip() if match else ""
            desired = f"/app/{slug}"
            if actual != desired:
                mismatches.append(
                    f"{slug}: config.xml UrlBase={actual!r} but Envoy "
                    f"routes {desired!r} — browser assets will 404",
                )

        self.assertFalse(
            mismatches,
            "Envoy-prefix vs UrlBase drift:\n  " + "\n  ".join(mismatches),
        )
        # absent_configs is informational — apps that haven't
        # written their config yet are expected during initial
        # bootstrap and NOT a test failure.

    def test_known_apps_have_an_envoy_route(self):
        """Sanity check — every *arr we care about SHOULD have a
        prefix registered. If this regresses, the dashboard's
        /app/<slug> links will start 404ing."""
        routed = _envoy_app_prefixes()
        missing = [slug for slug in _CONFIG_XML_APPS if slug not in routed]
        # This is a soft warning — a deploy without some *arrs
        # enabled is legitimate. Only flag when envoy.yaml exists
        # but the slug is absent from ALL routes (not just /app/).
        text = _ENVOY_YAML.read_text(encoding="utf-8")
        truly_missing = [s for s in missing if s not in text]
        self.assertFalse(
            truly_missing,
            f"These *arrs are completely absent from envoy.yaml: "
            f"{truly_missing}. Expected either a /app/<slug> route "
            f"or at least a subdomain virtual-host entry.",
        )


if __name__ == "__main__":
    unittest.main()
