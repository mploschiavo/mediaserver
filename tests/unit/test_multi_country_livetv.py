"""Tests for multi-country Live TV / IPTV support.

Verifies that multiple tuner sources and guide sources can be configured
simultaneously for different countries, persisted to the profile YAML,
and reconfigured without losing existing entries.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.config as config_mod  # noqa: E402


def _make_profile(data, td):
    import yaml
    p = Path(td) / "profile.yaml"
    with open(p, "w") as f:
        yaml.dump(data, f)
    return str(p)


class TestMultiCountryTuners(unittest.TestCase):
    """Test adding 3, 4, 5 country tuner sources simultaneously."""

    def test_three_countries(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with patch.object(config_mod, "resolve_profile_path", return_value=profile):
                result = config_mod.update_livetv_sources(tuners=[
                    {"url": "https://example.com/us.m3u", "name": "US IPTV"},
                    {"url": "https://example.com/gb.m3u", "name": "UK IPTV"},
                    {"url": "https://example.com/ca.m3u", "name": "Canada IPTV"},
                ])
        self.assertEqual(result["status"], "saved")
        self.assertEqual(len(result["tuners"]), 3)

    def test_four_countries(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with patch.object(config_mod, "resolve_profile_path", return_value=profile):
                result = config_mod.update_livetv_sources(tuners=[
                    {"url": "https://example.com/us.m3u", "name": "US"},
                    {"url": "https://example.com/gb.m3u", "name": "UK"},
                    {"url": "https://example.com/de.m3u", "name": "DE"},
                    {"url": "https://example.com/fr.m3u", "name": "FR"},
                ])
        self.assertEqual(len(result["tuners"]), 4)

    def test_five_countries_with_guides(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with patch.object(config_mod, "resolve_profile_path", return_value=profile):
                result = config_mod.update_livetv_sources(
                    tuners=[
                        {"url": "https://example.com/us.m3u", "name": "US"},
                        {"url": "https://example.com/gb.m3u", "name": "UK"},
                        {"url": "https://example.com/de.m3u", "name": "DE"},
                        {"url": "https://example.com/fr.m3u", "name": "FR"},
                        {"url": "https://example.com/jp.m3u", "name": "JP"},
                    ],
                    guides=[
                        {"url": "https://example.com/epg-us.xml", "name": "US EPG"},
                        {"url": "https://example.com/epg-gb.xml", "name": "UK EPG"},
                        {"url": "https://example.com/epg-de.xml", "name": "DE EPG"},
                        {"url": "https://example.com/epg-fr.xml", "name": "FR EPG"},
                        {"url": "https://example.com/epg-jp.xml", "name": "JP EPG"},
                    ],
                )
        self.assertEqual(len(result["tuners"]), 5)
        self.assertEqual(len(result["guides"]), 5)


class TestMultiCountryPersistence(unittest.TestCase):
    """Test that multi-country configs persist and reload correctly."""

    def test_persists_to_yaml(self):
        import yaml
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with patch.object(config_mod, "resolve_profile_path", return_value=profile):
                config_mod.update_livetv_sources(tuners=[
                    {"url": "https://example.com/us.m3u", "name": "US"},
                    {"url": "https://example.com/de.m3u", "name": "DE"},
                ])
            # Re-read the YAML
            data = yaml.safe_load(Path(profile).read_text())
            tuners = data.get("live_tv_defaults", {}).get("tuners", [])
            self.assertEqual(len(tuners), 2)
            self.assertEqual(tuners[0]["name"], "US")
            self.assertEqual(tuners[1]["name"], "DE")

    def test_read_back_matches(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with patch.object(config_mod, "resolve_profile_path", return_value=profile):
                config_mod.update_livetv_sources(tuners=[
                    {"url": "https://example.com/us.m3u", "name": "US"},
                    {"url": "https://example.com/gb.m3u", "name": "UK"},
                    {"url": "https://example.com/au.m3u", "name": "AU"},
                ], guides=[
                    {"url": "https://example.com/epg-us.xml", "name": "US EPG"},
                    {"url": "https://example.com/epg-gb.xml", "name": "UK EPG"},
                    {"url": "https://example.com/epg-au.xml", "name": "AU EPG"},
                ])
                result = config_mod.get_livetv_sources()
            self.assertEqual(len(result["tuners"]), 3)
            self.assertEqual(len(result["guides"]), 3)

    def test_backward_compat_single_url(self):
        """Single tuner_url still works for backward compatibility."""
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with patch.object(config_mod, "resolve_profile_path", return_value=profile):
                config_mod.update_livetv_sources(
                    tuner_url="https://example.com/us.m3u",
                    guide_url="https://example.com/epg-us.xml",
                )
                result = config_mod.get_livetv_sources()
            self.assertEqual(len(result["tuners"]), 1)
            self.assertEqual(result["tuner_url"], "https://example.com/us.m3u")


class TestReconfiguration(unittest.TestCase):
    """Test that reconfiguration replaces existing sources correctly."""

    def test_replace_all_tuners(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with patch.object(config_mod, "resolve_profile_path", return_value=profile):
                # First config: US + UK
                config_mod.update_livetv_sources(tuners=[
                    {"url": "https://example.com/us.m3u", "name": "US"},
                    {"url": "https://example.com/gb.m3u", "name": "UK"},
                ])
                # Reconfigure: DE + FR + ES
                config_mod.update_livetv_sources(tuners=[
                    {"url": "https://example.com/de.m3u", "name": "DE"},
                    {"url": "https://example.com/fr.m3u", "name": "FR"},
                    {"url": "https://example.com/es.m3u", "name": "ES"},
                ])
                result = config_mod.get_livetv_sources()
            self.assertEqual(len(result["tuners"]), 3)
            names = [t["name"] for t in result["tuners"]]
            self.assertNotIn("US", names)
            self.assertIn("DE", names)

    def test_empty_tuners_clears(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({}, td)
            with patch.object(config_mod, "resolve_profile_path", return_value=profile):
                config_mod.update_livetv_sources(tuners=[
                    {"url": "https://example.com/us.m3u", "name": "US"},
                ])
                config_mod.update_livetv_sources(tuners=[])
                result = config_mod.get_livetv_sources()
            self.assertEqual(len(result["tuners"]), 0)


class TestIptvCountriesApi(unittest.TestCase):
    """Test the IPTV countries API endpoint."""

    def test_returns_countries(self):
        with patch.object(config_mod, "resolve_profile_path", return_value=None):
            result = config_mod.get_iptv_countries()
        self.assertIn("countries", result)
        self.assertGreater(len(result["countries"]), 10)

    def test_countries_have_required_fields(self):
        with patch.object(config_mod, "resolve_profile_path", return_value=None):
            result = config_mod.get_iptv_countries()
        for c in result["countries"]:
            self.assertIn("code", c)
            self.assertIn("name", c)

    def test_countries_have_urls_when_template_set(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({
                "live_tv_defaults": {
                    "tuner_url_template": "https://example.com/iptv/{code}.m3u",
                    "guide_url_template": "https://example.com/epg/{code}.xml",
                }
            }, td)
            with patch.object(config_mod, "resolve_profile_path", return_value=profile):
                result = config_mod.get_iptv_countries()
        us = next(c for c in result["countries"] if c["code"] == "us")
        self.assertEqual(us["tuner_url"], "https://example.com/iptv/us.m3u")
        self.assertEqual(us["guide_url"], "https://example.com/epg/us.xml")

    def test_profile_overrides_country_list(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({
                "iptv_countries": [
                    {"code": "xx", "name": "Custom Country", "tuner_url": "https://custom/xx.m3u"},
                ]
            }, td)
            with patch.object(config_mod, "resolve_profile_path", return_value=profile):
                result = config_mod.get_iptv_countries()
        self.assertEqual(len(result["countries"]), 1)
        self.assertEqual(result["countries"][0]["code"], "xx")
        self.assertEqual(result["source"], "profile")


if __name__ == "__main__":
    unittest.main()
