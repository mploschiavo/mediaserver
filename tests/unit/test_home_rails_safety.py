"""Verify home screen uses native DisplayPreferences, not BoxSet collections.

REDESIGNED 2026-04-12: Home screen layout is now configured entirely through
DisplayPreferences (homesection0-9). BoxSet collections pollute library views
and are no longer created. The home_rails section only handles cleanup of
legacy collections.
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class TestHomeRailsDisabledByDefault(unittest.TestCase):
    """home_rails must be disabled until BoxSet pollution is fixed."""

    def test_home_rails_disabled_in_contract(self):
        contract = ROOT / "contracts" / "services" / "jellyfin.yaml"
        data = yaml.safe_load(contract.read_text())
        home_rails = data.get("defaults", {}).get("home_rails", {})
        self.assertFalse(
            home_rails.get("enabled", False),
            "home_rails must be disabled by default — BoxSet collections created by this "
            "feature appear inside Movies/TV library views, hiding actual content. "
            "Redesign to use DisplayPreferences before enabling.",
        )

    def test_cleanup_collections_when_disabled(self):
        """When disabled, home_rails should clean up its own collections."""
        contract = ROOT / "contracts" / "services" / "jellyfin.yaml"
        data = yaml.safe_load(contract.read_text())
        home_rails = data.get("defaults", {}).get("home_rails", {})
        self.assertTrue(
            home_rails.get("cleanup_collections_when_disabled", False),
            "cleanup_collections_when_disabled should be true so disabling "
            "home_rails removes the BoxSet collections it created.",
        )


class TestHomeScreenViaDisplayPreferences(unittest.TestCase):
    """Home screen must be configured through native Jellyfin sections."""

    VALID_SECTIONS = {
        "smalllibrarytiles", "resume", "nextup", "latestmedia",
        "livetv", "activerecordings", "none",
    }

    def test_homesections_in_display_prefs(self):
        """Home screen layout must use native section types in custom_prefs."""
        contract = ROOT / "contracts" / "services" / "jellyfin.yaml"
        data = yaml.safe_load(contract.read_text())
        prefs = data.get("defaults", {}).get("playback", {}).get("display_preferences", {})
        custom = prefs.get("custom_prefs", {})
        sections = {k: v for k, v in custom.items() if k.startswith("homesection")}
        self.assertGreater(len(sections), 0, "No homesection entries in custom_prefs")
        for key, value in sections.items():
            self.assertIn(value, self.VALID_SECTIONS,
                f"{key}={value} is not a valid native Jellyfin section. "
                f"Valid: {self.VALID_SECTIONS}")

    def test_resume_and_latest_are_enabled(self):
        """Continue Watching and Latest Media must be in the home layout."""
        contract = ROOT / "contracts" / "services" / "jellyfin.yaml"
        data = yaml.safe_load(contract.read_text())
        custom = data["defaults"]["playback"]["display_preferences"]["custom_prefs"]
        values = [v for k, v in custom.items() if k.startswith("homesection")]
        self.assertIn("resume", values, "Continue Watching (resume) must be on the home screen")
        self.assertIn("latestmedia", values, "Latest Media must be on the home screen")

    def test_per_library_prefs_exist(self):
        """Per-library display prefs (SortBy, SortOrder) should be configured."""
        contract = ROOT / "contracts" / "services" / "jellyfin.yaml"
        data = yaml.safe_load(contract.read_text())
        prefs = data["defaults"]["playback"]["display_preferences"]
        per_lib = prefs.get("per_library_prefs", {})
        self.assertIn("movies", per_lib, "Movies library needs per_library_prefs")
        self.assertIn("tv", per_lib, "TV library needs per_library_prefs")


if __name__ == "__main__":
    unittest.main()
