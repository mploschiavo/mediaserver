"""Verify home_rails is disabled by default and document why.

BoxSet collections created by home_rails appear IN library views (Movies, TV)
making it look like actual content is missing. Until the feature is redesigned
to use DisplayPreferences instead of BoxSets, it must stay disabled.
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


if __name__ == "__main__":
    unittest.main()
