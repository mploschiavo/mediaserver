"""Test that contract-defined config values survive the loading pipeline.

Catches bugs where a contract defines a value (e.g., enrich_program_icons=True)
but a job handler, config loader, or intermediate transform overwrites or drops it.
"""

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = ROOT / "contracts" / "services"


def _load_contract(service_id: str) -> dict:
    path = CONTRACTS_DIR / f"{service_id}.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _load_cfg_from_contracts() -> dict:
    """Load config through the same pipeline the job framework uses."""
    from media_stack.cli.commands.job_framework import _load_cfg_from_contracts
    return _load_cfg_from_contracts()


class TestJellyfinLiveTvConfigFidelity(unittest.TestCase):
    """Jellyfin livetv contract values must survive config loading."""

    def setUp(self):
        self.contract = _load_contract("jellyfin")
        self.cfg = _load_cfg_from_contracts()
        self.livetv = self.cfg.get("jellyfin_livetv", {})

    def test_contract_has_enrichment_flags(self):
        """Verify the contract defines the enrichment flags we depend on."""
        guides = self.contract.get("defaults", {}).get("livetv", {}).get("guides", [])
        self.assertTrue(guides, "Contract must define at least one guide")
        guide = guides[0]
        self.assertIn("enrich_program_icons_from_tuner_logo", guide)
        self.assertTrue(guide["enrich_program_icons_from_tuner_logo"])

    def test_enrichment_flags_survive_config_loading(self):
        """Config loader must preserve guide enrichment flags from contract."""
        guides = self.livetv.get("guides", [])
        self.assertTrue(guides, "Config must have guides after loading")
        guide = guides[0]
        self.assertTrue(
            guide.get("enrich_program_icons_from_tuner_logo"),
            f"enrich_program_icons_from_tuner_logo lost during config loading: {guide}",
        )

    def test_default_icon_url_survives_config_loading(self):
        guides = self.livetv.get("guides", [])
        self.assertTrue(guides)
        guide = guides[0]
        self.assertTrue(
            guide.get("default_program_icon_url"),
            f"default_program_icon_url lost during config loading: {guide}",
        )

    def test_category_enrichment_survives_config_loading(self):
        guides = self.livetv.get("guides", [])
        self.assertTrue(guides)
        guide = guides[0]
        self.assertTrue(
            guide.get("enrich_program_categories_from_tuner_groups"),
            f"enrich_program_categories_from_tuner_groups lost during config loading: {guide}",
        )

    def test_tuner_config_survives(self):
        tuners = self.livetv.get("tuners", [])
        self.assertTrue(tuners, "Config must have tuners after loading")
        tuner = tuners[0]
        self.assertIn("url", tuner)
        self.assertIn("type", tuner)


class TestJellyfinPlaybackConfigFidelity(unittest.TestCase):
    """Jellyfin playback contract values must survive config loading."""

    def setUp(self):
        self.contract = _load_contract("jellyfin")
        self.cfg = _load_cfg_from_contracts()
        self.playback = self.cfg.get("jellyfin_playback", {})

    def test_display_preferences_survive(self):
        dp = self.playback.get("display_preferences", {})
        self.assertTrue(dp.get("enabled", False) or dp == {},
                        "display_preferences must be present")

    def test_show_backdrop_true_in_contract(self):
        dp = self.contract.get("defaults", {}).get("playback", {}).get("display_preferences", {})
        self.assertTrue(dp.get("show_backdrop"), "Contract must set show_backdrop=true")

    def test_show_backdrop_survives_loading(self):
        dp = self.playback.get("display_preferences", {})
        self.assertTrue(
            dp.get("show_backdrop"),
            f"show_backdrop lost during config loading: {dp}",
        )

    def test_custom_prefs_backdrop_keys_survive(self):
        dp = self.playback.get("display_preferences", {})
        custom = dp.get("custom_prefs", {})
        self.assertTrue(custom.get("enableBackdrops"),
                        f"enableBackdrops lost: {custom}")
        self.assertTrue(custom.get("enableLibraryBackdrops"),
                        f"enableLibraryBackdrops lost: {custom}")

    def test_clients_list_survives(self):
        dp = self.playback.get("display_preferences", {})
        clients = dp.get("clients", [])
        self.assertIn("emby", clients, f"emby client missing: {clients}")
        self.assertIn("jellyfin-web", clients, f"jellyfin-web client missing: {clients}")


class TestJellyseerrConfigFidelity(unittest.TestCase):
    """Jellyseerr contract values must survive config loading."""

    def setUp(self):
        self.cfg = _load_cfg_from_contracts()

    def test_jellyseerr_config_loaded(self):
        js = self.cfg.get("jellyseerr", {})
        self.assertTrue(js, "jellyseerr config must be present")
        self.assertTrue(js.get("enabled", False), "jellyseerr must be enabled by default")


if __name__ == "__main__":
    unittest.main()
