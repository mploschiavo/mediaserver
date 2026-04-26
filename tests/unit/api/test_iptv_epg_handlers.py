"""Stable-subset assertions for the IPTV / EPG GET handlers.

What this catches
-----------------
Drift between the controller handlers and the openapi.yaml schemas
that ship with each release. The cross-cutting
``test_api_response_contract.py`` already validates the captured
fixtures against the spec; this file complements it with
*shape-level* assertions on the handler return values themselves —
the bits a UI tile depends on but a fixture-vs-spec diff alone won't
catch (e.g. "did somebody change `ok` to `up`?").

Each test exercises the service layer the handler delegates to:

  * ``/api/epg-health``      → ``epg_provider_service.run_health_check``
  * ``/api/epg-providers``   → ``epg_provider_service.get_guide_providers``
                              + ``_load_health_cache``
  * ``/api/iptv-countries``  → ``LiveTvConfigService.get_iptv_countries``

Network probes are mocked to keep the suite hermetic (otherwise
``run_health_check`` would walk every provider for every country).
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

_FIXTURES = ROOT / "tests" / "fixtures" / "api_responses"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# /api/epg-providers — service-layer shape
# ---------------------------------------------------------------------------


class TestEpgProvidersHandlerShape(unittest.TestCase):
    """``get_guide_providers`` + ``_load_health_cache`` produce the
    keys the dashboard's EPG table reads. Pin them so a rename in
    ``epg_provider_service`` fails here before it ships."""

    def test_get_guide_providers_returns_list_with_required_fields(self) -> None:
        from media_stack.services.epg_provider_service import (
            get_guide_providers,
            invalidate_cache,
        )
        invalidate_cache()
        providers = get_guide_providers()
        self.assertIsInstance(providers, list)
        self.assertGreater(len(providers), 0)
        for entry in providers:
            # Required fields per EpgProviderEntry schema in openapi.yaml.
            for required in ("id", "name", "format", "priority", "enabled"):
                self.assertIn(
                    required, entry,
                    f"provider {entry.get('id', '?')} missing {required}",
                )
            self.assertIsInstance(entry["id"], str)
            self.assertIsInstance(entry["name"], str)
            self.assertIsInstance(entry["priority"], int)
            self.assertIsInstance(entry["enabled"], bool)

    def test_providers_sorted_by_priority(self) -> None:
        """Resolution order matters: priority 1 is tried first."""
        from media_stack.services.epg_provider_service import (
            get_guide_providers,
            invalidate_cache,
        )
        invalidate_cache()
        priorities = [p["priority"] for p in get_guide_providers()]
        self.assertEqual(priorities, sorted(priorities))

    def test_health_cache_entries_have_required_fields(self) -> None:
        """Every cache entry must carry ``ok``/``ts``/``url`` — the
        UI keys both health rollups and the per-row "last checked"
        column off these. Captured fixture is the source of truth
        for the wire format."""
        fixture = _load_fixture("epg-providers")
        self.assertIn("health", fixture)
        self.assertIn("providers", fixture)
        self.assertIsInstance(fixture["health"], dict)
        for cache_key, entry in fixture["health"].items():
            self.assertRegex(
                cache_key, r"^[a-z0-9-]+:[a-z]{2}$",
                f"unexpected health-cache key shape: {cache_key!r}",
            )
            for required in ("ok", "ts", "url"):
                self.assertIn(required, entry, f"{cache_key} missing {required}")
            self.assertIsInstance(entry["ok"], bool)
            self.assertIsInstance(entry["ts"], (int, float))
            self.assertIsInstance(entry["url"], str)


# ---------------------------------------------------------------------------
# /api/epg-health — run_health_check shape
# ---------------------------------------------------------------------------


class TestEpgHealthHandlerShape(unittest.TestCase):
    """``run_health_check`` is the synchronous probe handler. It must
    return aggregate counts AND a per-country/per-provider matrix.
    Mock the network so the test runs offline in <100ms."""

    def test_run_health_check_returns_required_keys(self) -> None:
        from media_stack.services import epg_provider_service as _epg
        _epg.invalidate_cache()
        with patch.object(_epg._instance, "_probe_url", return_value=True):
            with patch.object(_epg._instance, "_save_health_cache"):
                result = _epg.run_health_check()
        for required in ("healthy", "unhealthy", "countries", "providers", "details"):
            self.assertIn(required, result, f"missing {required}")
        self.assertIsInstance(result["healthy"], int)
        self.assertIsInstance(result["unhealthy"], int)
        self.assertIsInstance(result["countries"], int)
        self.assertIsInstance(result["providers"], int)
        self.assertIsInstance(result["details"], dict)

    def test_details_matrix_is_country_then_provider(self) -> None:
        """Outer key = lowercase country code; inner key = provider id;
        values = booleans. Mismatching this nesting breaks the SPA's
        EPG-health heatmap (each row is a country)."""
        from media_stack.services import epg_provider_service as _epg
        _epg.invalidate_cache()
        with patch.object(_epg._instance, "_probe_url", return_value=False):
            with patch.object(_epg._instance, "_save_health_cache"):
                result = _epg.run_health_check()
        for country_code, providers in result["details"].items():
            self.assertRegex(country_code, r"^[a-z]{2}$")
            self.assertIsInstance(providers, dict)
            for provider_id, ok in providers.items():
                self.assertIsInstance(provider_id, str)
                self.assertIsInstance(ok, bool)

    def test_aggregate_counts_match_details_truthiness(self) -> None:
        """`healthy` must equal the number of `True` cells in
        `details`; symmetric for `unhealthy`. Drift between the
        scalar rollup and the matrix means one of them is lying —
        the dashboard tile and the heatmap would disagree."""
        from media_stack.services import epg_provider_service as _epg
        _epg.invalidate_cache()
        with patch.object(_epg._instance, "_probe_url", return_value=True):
            with patch.object(_epg._instance, "_save_health_cache"):
                result = _epg.run_health_check()
        cell_true = sum(
            1
            for providers in result["details"].values()
            for ok in providers.values()
            if ok
        )
        cell_false = sum(
            1
            for providers in result["details"].values()
            for ok in providers.values()
            if not ok
        )
        self.assertEqual(result["healthy"], cell_true)
        self.assertEqual(result["unhealthy"], cell_false)


# ---------------------------------------------------------------------------
# /api/iptv-countries — service-layer shape
# ---------------------------------------------------------------------------


class TestIptvCountriesHandlerShape(unittest.TestCase):
    """The handler delegates to ``LiveTvConfigService.get_iptv_countries``
    via ``config_svc.get_iptv_countries()``. Must always return a
    dict with `countries` (list of preset rows) and `source`
    (`profile`|`defaults`). Each row must carry the four fields the
    SPA's IPTV picker reads."""

    def setUp(self) -> None:
        # Drop any profile parsed by a prior test so we exercise the
        # default-catalogue branch deterministically when no
        # BOOTSTRAP_PROFILE_FILE is set.
        from media_stack.api.services import config as _cfg
        _cfg._invalidate_profile_cache()
        # Force the no-profile branch so the test outcome doesn't
        # depend on the developer's local env or an installed image
        # profile under /opt/media-stack.
        self._env_patch = patch.dict(
            os.environ, {"BOOTSTRAP_PROFILE_FILE": ""}, clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self._image_patch = patch(
            "media_stack.api.services._resolve._IMAGE_PROFILE",
            "/nonexistent/profile.yaml",
        )
        self._image_patch.start()
        self.addCleanup(self._image_patch.stop)

    def test_get_iptv_countries_returns_required_keys(self) -> None:
        from media_stack.api.services.config import get_iptv_countries
        result = get_iptv_countries()
        self.assertIn("countries", result)
        self.assertIn("source", result)
        self.assertIsInstance(result["countries"], list)
        # `source` must be one of the documented values; the schema
        # enum is enforced by the contract test, but pin it here too
        # so a handler-side change is caught in a focused test.
        self.assertIn(result["source"], ("profile", "defaults"))

    def test_each_country_has_required_fields(self) -> None:
        from media_stack.api.services.config import get_iptv_countries
        result = get_iptv_countries()
        self.assertGreater(
            len(result["countries"]), 0,
            "defaults catalogue should never be empty",
        )
        for entry in result["countries"]:
            for required in ("code", "name", "guide_url", "tuner_url"):
                self.assertIn(required, entry, f"missing {required} in {entry!r}")
            self.assertRegex(
                entry["code"], r"^[a-z]{2,3}$",
                f"unexpected country code shape: {entry['code']!r}",
            )
            # `tuner_url` and `guide_url` may be empty when no
            # provider supports the country, but they MUST be
            # strings — the SPA does direct string interpolation.
            self.assertIsInstance(entry["guide_url"], str)
            self.assertIsInstance(entry["tuner_url"], str)


# ---------------------------------------------------------------------------
# Captured-fixture sanity: the committed fixtures must conform to the
# stable subsets above. Keeps "fixture happens to drift away from the
# wire shape we last saw" honest.
# ---------------------------------------------------------------------------


class TestCapturedFixturesMatchStableSubset(unittest.TestCase):
    def test_epg_health_fixture(self) -> None:
        body = _load_fixture("epg-health")
        for key in ("healthy", "unhealthy", "countries", "providers", "details"):
            self.assertIn(key, body)
        self.assertIsInstance(body["details"], dict)

    def test_epg_providers_fixture(self) -> None:
        body = _load_fixture("epg-providers")
        self.assertIn("providers", body)
        self.assertIn("health", body)
        self.assertIsInstance(body["providers"], list)
        for entry in body["providers"]:
            for required in ("id", "name", "format", "priority", "enabled"):
                self.assertIn(required, entry, f"missing {required}")

    def test_iptv_countries_fixture(self) -> None:
        body = _load_fixture("iptv-countries")
        self.assertIn("countries", body)
        self.assertIn("source", body)
        self.assertIn(body["source"], ("profile", "defaults"))
        for entry in body["countries"]:
            for required in ("code", "name", "guide_url", "tuner_url"):
                self.assertIn(required, entry)


if __name__ == "__main__":
    unittest.main()
