"""Tests for the EPG provider registry and multi-source fallback."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


class TestProviderRegistry(unittest.TestCase):
    """epg_providers.yaml must exist and be valid."""

    PROVIDERS_PATH = ROOT / "contracts" / "epg_providers.yaml"

    def test_providers_yaml_exists(self):
        self.assertTrue(self.PROVIDERS_PATH.is_file())

    def test_providers_yaml_valid(self):
        import yaml
        data = yaml.safe_load(self.PROVIDERS_PATH.read_text())
        self.assertIn("guide_providers", data)
        self.assertIn("tuner_providers", data)
        self.assertIsInstance(data["guide_providers"], list)
        self.assertGreater(len(data["guide_providers"]), 0)

    def test_every_provider_has_required_fields(self):
        import yaml
        data = yaml.safe_load(self.PROVIDERS_PATH.read_text())
        for p in data["guide_providers"]:
            self.assertIn("id", p, f"Provider missing id: {p}")
            self.assertIn("priority", p, f"Provider {p['id']} missing priority")
            self.assertIn("format", p, f"Provider {p['id']} missing format")
            # Enabled providers must have url_template OR country_urls
            if p.get("enabled", True):
                has_template = bool(p.get("url_template"))
                has_urls = bool(p.get("country_urls"))
                self.assertTrue(has_template or has_urls,
                                f"Enabled provider {p['id']} has neither url_template nor country_urls")

    def test_priorities_are_unique(self):
        import yaml
        data = yaml.safe_load(self.PROVIDERS_PATH.read_text())
        priorities = [p["priority"] for p in data["guide_providers"]]
        self.assertEqual(len(priorities), len(set(priorities)),
                         f"Duplicate priorities: {priorities}")

    def test_at_least_3_guide_providers(self):
        """We want multiple fallback sources."""
        import yaml
        data = yaml.safe_load(self.PROVIDERS_PATH.read_text())
        enabled = [p for p in data["guide_providers"] if p.get("enabled", True)]
        self.assertGreaterEqual(len(enabled), 3,
                                f"Only {len(enabled)} enabled providers — need at least 3 for reliable fallback")


class TestProviderService(unittest.TestCase):
    """Test the EPG provider resolution logic."""

    def test_load_providers(self):
        from media_stack.services.epg_provider_service import _load_providers, invalidate_cache
        invalidate_cache()
        data = _load_providers()
        self.assertIn("guide_providers", data)

    def test_get_guide_providers_sorted(self):
        from media_stack.services.epg_provider_service import get_guide_providers, invalidate_cache
        invalidate_cache()
        providers = get_guide_providers()
        priorities = [p["priority"] for p in providers]
        self.assertEqual(priorities, sorted(priorities))

    def test_expand_url_template(self):
        from media_stack.services.epg_provider_service import _expand_url
        provider = {"url_template": "https://example.com/epg-{code}.xml"}
        self.assertEqual(_expand_url(provider, "us"), "https://example.com/epg-us.xml")
        self.assertEqual(_expand_url(provider, "GB"), "https://example.com/epg-gb.xml")

    def test_expand_url_uppercase(self):
        from media_stack.services.epg_provider_service import _expand_url
        provider = {"url_template": "https://example.com/epg_{CODE}.xml.gz"}
        self.assertEqual(_expand_url(provider, "us"), "https://example.com/epg_US.xml.gz")

    def test_expand_url_country_urls_override(self):
        from media_stack.services.epg_provider_service import _expand_url
        provider = {
            "url_template": "https://default.com/{code}.xml",
            "country_urls": {"gb": "https://special.com/uk.xml"},
        }
        self.assertEqual(_expand_url(provider, "gb"), "https://special.com/uk.xml")
        self.assertEqual(_expand_url(provider, "us"), "https://default.com/us.xml")

    def test_expand_url_no_template_no_urls(self):
        from media_stack.services.epg_provider_service import _expand_url
        self.assertEqual(_expand_url({}, "us"), "")

    def test_resolve_guide_url_returns_string(self):
        from media_stack.services.epg_provider_service import resolve_guide_url, invalidate_cache
        invalidate_cache()
        # Mock the probe to always return True for first provider
        with patch("media_stack.services.epg_provider_service._probe_url", return_value=True):
            url = resolve_guide_url("us")
        self.assertIn("us", url.lower() or "US")
        self.assertTrue(url.startswith("http"))

    def test_resolve_guide_url_falls_back(self):
        """If first provider fails, should try the next."""
        from media_stack.services.epg_provider_service import resolve_guide_url, invalidate_cache
        invalidate_cache()
        call_count = {"n": 0}

        def mock_probe(url, timeout=10):
            call_count["n"] += 1
            # First provider fails, second succeeds
            return call_count["n"] > 1

        with patch("media_stack.services.epg_provider_service._probe_url", side_effect=mock_probe):
            with patch("media_stack.services.epg_provider_service._load_health_cache", return_value={}):
                url = resolve_guide_url("us")
        self.assertTrue(url, "Should have found a fallback provider")
        self.assertGreater(call_count["n"], 1, "Should have tried multiple providers")


class TestExtractCountryCode(unittest.TestCase):
    def test_from_name(self):
        from media_stack.cli.commands.job_framework import _extract_country_code
        self.assertEqual(_extract_country_code("Germany EPG", ""), "de")
        self.assertEqual(_extract_country_code("China IPTV", ""), "cn")
        self.assertEqual(_extract_country_code("United Kingdom EPG", ""), "gb")

    def test_from_url(self):
        from media_stack.cli.commands.job_framework import _extract_country_code
        self.assertEqual(_extract_country_code("", "https://example.com/epg-us.xml"), "us")
        self.assertEqual(_extract_country_code("", "https://example.com/epg_DE.xml.gz"), "de")

    def test_unknown(self):
        from media_stack.cli.commands.job_framework import _extract_country_code
        self.assertEqual(_extract_country_code("Unknown", ""), "")


class TestUrlLooksValid(unittest.TestCase):
    def test_valid(self):
        from media_stack.cli.commands.job_framework import _url_looks_valid
        self.assertTrue(_url_looks_valid("https://example.com/file.xml"))
        self.assertTrue(_url_looks_valid("http://example.com/file.xml"))

    def test_invalid(self):
        from media_stack.cli.commands.job_framework import _url_looks_valid
        self.assertFalse(_url_looks_valid("/epg/ca.xml"))
        self.assertFalse(_url_looks_valid(""))
        self.assertFalse(_url_looks_valid("relative/path.xml"))


if __name__ == "__main__":
    unittest.main()
