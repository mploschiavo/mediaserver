import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.stack.controller_config_policy import (  # noqa: E402
    _set_bool_path,
    _set_enabled,
    _slugify,
    _tokenize,
    _walk_path,
    apply_api_key_policy,
    apply_bootstrap_runtime_policy,
    apply_content_download_policy,
    parse_selected_apps_csv,
)


class TestTokenize(unittest.TestCase):
    def test_lowercases_and_strips_non_alnum(self):
        self.assertEqual(_tokenize("Hello-World!"), "helloworld")

    def test_empty_string(self):
        self.assertEqual(_tokenize(""), "")

    def test_none_returns_empty(self):
        self.assertEqual(_tokenize(None), "")

    def test_preserves_digits(self):
        self.assertEqual(_tokenize("app123"), "app123")

    def test_strips_whitespace_and_special(self):
        self.assertEqual(_tokenize("  My App! "), "myapp")


class TestSlugify(unittest.TestCase):
    def test_preserves_hyphens(self):
        self.assertEqual(_slugify("media-stack-controller"), "media-stack-controller")

    def test_lowercases(self):
        self.assertEqual(_slugify("MY-APP"), "my-app")

    def test_strips_non_alnum_non_hyphen(self):
        self.assertEqual(_slugify("app_name!"), "appname")

    def test_strips_trailing_hyphens(self):
        self.assertEqual(_slugify("app---"), "app")

    def test_empty_returns_empty(self):
        self.assertEqual(_slugify(""), "")

    def test_none_returns_empty(self):
        self.assertEqual(_slugify(None), "")


class TestParseSelectedAppsCsv(unittest.TestCase):
    def test_basic_csv(self):
        result = parse_selected_apps_csv("radarr,sonarr,prowlarr")
        self.assertEqual(result, {"radarr", "sonarr", "prowlarr"})

    def test_empty_string_returns_empty_set(self):
        self.assertEqual(parse_selected_apps_csv(""), set())

    def test_none_returns_empty_set(self):
        self.assertEqual(parse_selected_apps_csv(None), set())

    def test_whitespace_and_case(self):
        result = parse_selected_apps_csv(" Radarr , SONARR ")
        self.assertEqual(result, {"radarr", "sonarr"})

    def test_duplicate_tokens_deduplicated(self):
        result = parse_selected_apps_csv("radarr,radarr,sonarr")
        self.assertEqual(result, {"radarr", "sonarr"})

    def test_empty_segments_ignored(self):
        result = parse_selected_apps_csv(",radarr,,sonarr,")
        self.assertEqual(result, {"radarr", "sonarr"})


class TestSetEnabled(unittest.TestCase):
    def test_sets_enabled_true(self):
        section = {"enabled": False}
        _set_enabled(section, True)
        self.assertTrue(section["enabled"])

    def test_sets_enabled_false(self):
        section = {"enabled": True}
        _set_enabled(section, False)
        self.assertFalse(section["enabled"])

    def test_no_enabled_key_is_noop(self):
        section = {"name": "test"}
        _set_enabled(section, True)
        self.assertNotIn("enabled", section)

    def test_none_section_is_noop(self):
        # Should not raise
        _set_enabled(None, True)

    def test_non_dict_is_noop(self):
        _set_enabled("not a dict", True)


class TestWalkPath(unittest.TestCase):
    def test_simple_path(self):
        cfg = {"a": {"b": {"c": 1}}}
        result = _walk_path(cfg, "a.b")
        self.assertEqual(result, {"c": 1})

    def test_leaf_is_not_dict_returns_none(self):
        cfg = {"a": {"b": 42}}
        self.assertIsNone(_walk_path(cfg, "a.b"))

    def test_missing_key_returns_none(self):
        cfg = {"a": {"b": 1}}
        self.assertIsNone(_walk_path(cfg, "a.x"))

    def test_empty_path_returns_none(self):
        cfg = {"a": 1}
        self.assertIsNone(_walk_path(cfg, ""))

    def test_single_segment(self):
        cfg = {"foo": {"bar": 1}}
        result = _walk_path(cfg, "foo")
        self.assertEqual(result, {"bar": 1})


class TestSetBoolPath(unittest.TestCase):
    def test_sets_nested_bool(self):
        cfg = {"a": {"b": {"c": False}}}
        _set_bool_path(cfg, "a.b.c", True)
        self.assertTrue(cfg["a"]["b"]["c"])

    def test_sets_top_level_bool(self):
        cfg = {"flag": False}
        _set_bool_path(cfg, "flag", True)
        self.assertTrue(cfg["flag"])

    def test_missing_parent_is_noop(self):
        cfg = {"a": 1}
        _set_bool_path(cfg, "a.b.c", True)
        # Should not raise; a is not a dict so nothing happens
        self.assertEqual(cfg["a"], 1)

    def test_empty_path_is_noop(self):
        cfg = {"a": 1}
        _set_bool_path(cfg, "", True)
        self.assertEqual(cfg, {"a": 1})


# Stub the policy catalog to avoid filesystem/registry dependency
_STUB_POLICY = {
    "selected_apps_policy": {
        "app_toggle_sections": {
            "jellyfin": "jellyfin",
            "homepage": "homepage",
            "maintainerr": "maintainerr",
        },
        "arr_app_keys": ["radarr", "sonarr"],
        "selected_app_expansions": {},
        "arr_disable_sections_when_unselected": [
            "arr_media_management",
            "arr_download_handling",
        ],
        "arr_discovery_reserved_keys": ["enabled", "trigger_initial_sync"],
        "homepage_host_reserved_tokens": [],
        "jellyfin_disable_sections_when_unselected": ["jellyfin_home_rails"],
        "maintainerr_integrations_section": "maintainerr.integrations",
        "jellyfin_home_rails_cleanup_path": "jellyfin_home_rails.cleanup_collections_when_disabled",
    }
}


def _patch_policy():
    return patch(
        "media_stack.services.apps.stack.controller_config_policy._load_policy_catalog",
        return_value=_STUB_POLICY,
    )


class TestApplyApiKeyPolicy(unittest.TestCase):
    def test_disables_app_auth_when_opted_out(self):
        cfg = {"app_auth": {"enabled": True}}
        apply_api_key_policy(cfg, preconfigure_api_keys=False)
        self.assertFalse(cfg["app_auth"]["enabled"])

    def test_leaves_app_auth_when_opted_in(self):
        cfg = {"app_auth": {"enabled": True}}
        apply_api_key_policy(cfg, preconfigure_api_keys=True)
        self.assertTrue(cfg["app_auth"]["enabled"])


class TestApplyContentDownloadPolicy(unittest.TestCase):
    def test_enables_auto_download(self):
        cfg = {
            "prowlarr_auto_add_tested_indexers": False,
            "arr_discovery_lists": {
                "trigger_initial_sync": False,
                "radarr": [{"enable_auto": False, "search_on_add": False}],
            },
            "sonarr_seed_series": {"enabled": False, "search_for_missing_episodes": False},
        }
        apply_content_download_policy(cfg, auto_download_content=True)
        self.assertTrue(cfg["prowlarr_auto_add_tested_indexers"])
        self.assertTrue(cfg["arr_discovery_lists"]["trigger_initial_sync"])
        self.assertTrue(cfg["arr_discovery_lists"]["radarr"][0]["enable_auto"])
        self.assertTrue(cfg["sonarr_seed_series"]["enabled"])

    def test_disables_auto_download(self):
        cfg = {
            "prowlarr_auto_add_tested_indexers": True,
            "arr_discovery_lists": {
                "trigger_initial_sync": True,
                "sonarr": [{"enable_auto": True, "should_search": True}],
            },
            "sonarr_seed_series": {"enabled": True, "search_for_missing_episodes": True},
        }
        apply_content_download_policy(cfg, auto_download_content=False)
        self.assertFalse(cfg["prowlarr_auto_add_tested_indexers"])
        self.assertFalse(cfg["arr_discovery_lists"]["trigger_initial_sync"])
        self.assertFalse(cfg["arr_discovery_lists"]["sonarr"][0]["enable_auto"])
        self.assertFalse(cfg["sonarr_seed_series"]["enabled"])

    def test_prevent_search_set_on_jellyseerr(self):
        cfg = {
            "prowlarr_auto_add_tested_indexers": False,
            "jellyseerr": {
                "radarr": {"prevent_search": True},
                "sonarr": {"prevent_search": True},
            },
        }
        apply_content_download_policy(cfg, auto_download_content=True)
        self.assertFalse(cfg["jellyseerr"]["radarr"]["prevent_search"])
        self.assertFalse(cfg["jellyseerr"]["sonarr"]["prevent_search"])


class TestApplyBootstrapRuntimePolicy(unittest.TestCase):
    @_patch_policy()
    def test_full_pipeline_runs_without_error(self, _mock):
        cfg = {
            "jellyfin": {"enabled": True},
            "homepage": {"enabled": True, "hosts": []},
            "maintainerr": {"enabled": True, "integrations": {"enabled": True}},
            "arr_apps": [],
            "prowlarr_auto_add_tested_indexers": False,
            "app_auth": {"enabled": True, "include": []},
        }
        # Should not raise
        apply_bootstrap_runtime_policy(
            cfg,
            selected_apps_csv="jellyfin,homepage",
            preconfigure_api_keys=True,
            auto_download_content=False,
            internet_exposed=False,
            route_strategy="path-prefix",
            app_gateway_host="gateway.local",
            app_path_prefix="/app",
        )

    @_patch_policy()
    def test_unselected_app_gets_disabled(self, _mock):
        cfg = {
            "jellyfin": {"enabled": True},
            "homepage": {"enabled": True, "hosts": []},
            "maintainerr": {"enabled": True, "integrations": {"enabled": True}},
            "arr_apps": [],
            "arr_media_management": {"enabled": True},
            "arr_download_handling": {"enabled": True},
            "prowlarr_url": "http://prowlarr:9696",
            "prowlarr_indexers": [{"name": "idx"}],
            "trigger_indexer_sync": True,
            "prowlarr_auto_add_tested_indexers": True,
            "flaresolverr": {"enabled": True},
            "jellyfin_home_rails": {"enabled": True, "cleanup_collections_when_disabled": True},
            "app_auth": {"enabled": True, "include": ["jellyfin"]},
        }
        apply_bootstrap_runtime_policy(
            cfg,
            selected_apps_csv="homepage",
        )
        # Jellyfin was not selected so its sections should be disabled
        self.assertFalse(cfg["jellyfin"]["enabled"])
        self.assertFalse(cfg["jellyfin_home_rails"]["enabled"])
        # Maintainerr integrations should be disabled
        self.assertFalse(cfg["maintainerr"]["integrations"]["enabled"])


if __name__ == "__main__":
    unittest.main()
