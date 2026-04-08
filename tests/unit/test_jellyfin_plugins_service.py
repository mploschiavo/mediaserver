"""Tests for JellyfinPluginsService."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyfin.plugins_service import (  # noqa: E402
    JellyfinPluginsDependencies,
    JellyfinPluginsService,
)


def _make_deps(**overrides):
    defaults = {
        "log": mock.Mock(),
        "bool_cfg": lambda cfg, key, default: cfg.get(key, default),
        "coerce_list": lambda v: list(v) if isinstance(v, (list, tuple)) else ([v] if v else []),
        "normalize_url": lambda u: u.rstrip("/"),
        "wait_for_service": mock.Mock(),
        "resolve_api_key": mock.Mock(return_value="test-api-key"),
        "jellyfin_request": mock.Mock(return_value=(200, [], "")),
    }
    defaults.update(overrides)
    return JellyfinPluginsDependencies(**defaults)


def _make_service(**overrides):
    deps = _make_deps(**overrides)
    return JellyfinPluginsService(deps=deps), deps


# ---------------------------------------------------------------------------
# normalize_plugin_name
# ---------------------------------------------------------------------------


class TestNormalizePluginName(unittest.TestCase):
    def test_lowercase_alpha(self):
        self.assertEqual(JellyfinPluginsService.normalize_plugin_name("OpenSubtitles"), "opensubtitles")

    def test_strips_spaces(self):
        self.assertEqual(JellyfinPluginsService.normalize_plugin_name("  Open  Subtitles  "), "opensubtitles")

    def test_removes_special_chars(self):
        self.assertEqual(JellyfinPluginsService.normalize_plugin_name("Plugin-Name_v2.0"), "pluginnamev20")

    def test_empty_string(self):
        self.assertEqual(JellyfinPluginsService.normalize_plugin_name(""), "")

    def test_none_value(self):
        self.assertEqual(JellyfinPluginsService.normalize_plugin_name(None), "")

    def test_numeric_string(self):
        self.assertEqual(JellyfinPluginsService.normalize_plugin_name("123"), "123")

    def test_all_special_chars(self):
        self.assertEqual(JellyfinPluginsService.normalize_plugin_name("---!!!---"), "")


# ---------------------------------------------------------------------------
# ensure_plugin_repositories
# ---------------------------------------------------------------------------


class TestEnsurePluginRepositories(unittest.TestCase):
    def test_empty_repositories_skips(self):
        svc, deps = _make_service()
        svc.ensure_plugin_repositories("http://jf:8096", "key", [])
        deps.jellyfin_request.assert_not_called()

    def test_none_repositories_skips(self):
        svc, deps = _make_service()
        svc.ensure_plugin_repositories("http://jf:8096", "key", None)
        deps.jellyfin_request.assert_not_called()

    def test_non_dict_items_filtered(self):
        svc, deps = _make_service()
        svc.ensure_plugin_repositories("http://jf:8096", "key", ["not-a-dict", 42])
        deps.jellyfin_request.assert_not_called()

    def test_adds_new_repository(self):
        calls = []

        def fake_request(url, path, api_key, **kwargs):
            calls.append((path, kwargs.get("method")))
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            return 204, None, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        svc.ensure_plugin_repositories(
            "http://jf:8096", "key",
            [{"name": "Test Repo", "url": "https://example.com/manifest.json"}],
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0], "/Repositories")
        self.assertEqual(calls[1][1], "POST")

    def test_no_change_when_repo_exists_with_same_config(self):
        existing = [{"Name": "Test Repo", "Url": "https://example.com/manifest.json", "Enabled": True}]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, existing, ""
            return 204, None, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        svc.ensure_plugin_repositories(
            "http://jf:8096", "key",
            [{"name": "Test Repo", "url": "https://example.com/manifest.json", "enabled": True}],
        )
        deps.log.assert_called_with("[OK] Jellyfin plugins: repositories already match desired config")

    def test_updates_repo_when_name_differs(self):
        existing = [{"Name": "Old Name", "Url": "https://example.com/manifest.json", "Enabled": True}]
        post_payload = None

        def fake_request(url, path, api_key, **kwargs):
            nonlocal post_payload
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, existing, ""
            if kwargs.get("method") == "POST":
                post_payload = kwargs.get("payload")
                return 204, None, ""
            return 200, [], ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        svc.ensure_plugin_repositories(
            "http://jf:8096", "key",
            [{"name": "New Name", "url": "https://example.com/manifest.json"}],
        )
        self.assertIsNotNone(post_payload)
        self.assertEqual(post_payload[0]["Name"], "New Name")

    def test_updates_repo_when_enabled_differs(self):
        existing = [{"Name": "Repo", "Url": "https://example.com/manifest.json", "Enabled": True}]
        post_payload = None

        def fake_request(url, path, api_key, **kwargs):
            nonlocal post_payload
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, existing, ""
            if kwargs.get("method") == "POST":
                post_payload = kwargs.get("payload")
                return 204, None, ""
            return 200, [], ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        svc.ensure_plugin_repositories(
            "http://jf:8096", "key",
            [{"name": "Repo", "url": "https://example.com/manifest.json", "enabled": False}],
        )
        self.assertIsNotNone(post_payload)
        self.assertFalse(post_payload[0]["Enabled"])

    def test_list_repositories_failure_raises(self):
        svc, _ = _make_service(
            jellyfin_request=mock.Mock(return_value=(500, None, "Server error")),
        )
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure_plugin_repositories("http://jf:8096", "key", [{"url": "https://x.com"}])
        self.assertIn("failed listing repositories", str(ctx.exception))

    def test_update_repositories_failure_raises(self):
        call_count = [0]

        def fake_request(url, path, api_key, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return 200, [], ""
            return 500, None, "Internal error"

        svc, _ = _make_service(jellyfin_request=fake_request)
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure_plugin_repositories(
                "http://jf:8096", "key",
                [{"url": "https://example.com/manifest.json"}],
            )
        self.assertIn("failed updating repositories", str(ctx.exception))

    def test_url_normalization_case_insensitive(self):
        existing = [{"Name": "Repo", "Url": "https://EXAMPLE.COM/Manifest.json", "Enabled": True}]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, existing, ""
            return 204, None, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        svc.ensure_plugin_repositories(
            "http://jf:8096", "key",
            [{"name": "Repo", "url": "https://example.com/manifest.json"}],
        )
        deps.log.assert_called_with("[OK] Jellyfin plugins: repositories already match desired config")

    def test_skips_existing_repos_with_empty_url(self):
        existing = [{"Name": "No URL", "Url": "", "Enabled": True}]

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, existing, ""
            return 204, None, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        svc.ensure_plugin_repositories(
            "http://jf:8096", "key",
            [{"url": "https://example.com/manifest.json"}],
        )


# ---------------------------------------------------------------------------
# find_package
# ---------------------------------------------------------------------------


class TestFindPackage(unittest.TestCase):
    def _packages(self):
        return [
            {"name": "OpenSubtitles", "guid": "g1", "versions": [{"repositoryUrl": "https://r1.com"}]},
            {"name": "Fanart", "guid": "g2", "versions": [{"repositoryUrl": "https://r2.com"}]},
            {"Name": "TMDb Box Sets", "Guid": "g3", "Versions": [{"RepositoryUrl": "https://r1.com"}]},
        ]

    def test_exact_match_by_name(self):
        svc, _ = _make_service()
        result = svc.find_package(self._packages(), "OpenSubtitles")
        self.assertIsNotNone(result)
        self.assertEqual(result["guid"], "g1")

    def test_exact_match_case_insensitive(self):
        svc, _ = _make_service()
        result = svc.find_package(self._packages(), "opensubtitles")
        self.assertIsNotNone(result)
        self.assertEqual(result["guid"], "g1")

    def test_normalized_match_removes_special_chars(self):
        svc, _ = _make_service()
        result = svc.find_package(self._packages(), "TMDb-Box-Sets")
        self.assertIsNotNone(result)
        self.assertEqual(result["Guid"], "g3")

    def test_no_match_returns_none(self):
        svc, _ = _make_service()
        result = svc.find_package(self._packages(), "NonExistent")
        self.assertIsNone(result)

    def test_filter_by_repository_url(self):
        svc, _ = _make_service()
        result = svc.find_package(self._packages(), "OpenSubtitles", "https://r1.com")
        self.assertIsNotNone(result)
        self.assertEqual(result["guid"], "g1")

    def test_filter_by_repository_url_no_match(self):
        svc, _ = _make_service()
        result = svc.find_package(self._packages(), "OpenSubtitles", "https://no-match.com")
        self.assertIsNone(result)

    def test_empty_packages_list(self):
        svc, _ = _make_service()
        result = svc.find_package([], "OpenSubtitles")
        self.assertIsNone(result)

    def test_packages_with_empty_names_skipped(self):
        svc, _ = _make_service()
        packages = [{"name": "", "guid": "g1"}, {"name": "Valid", "guid": "g2"}]
        result = svc.find_package(packages, "Valid")
        self.assertIsNotNone(result)
        self.assertEqual(result["guid"], "g2")

    def test_prefers_exact_match_over_normalized(self):
        svc, _ = _make_service()
        packages = [
            {"name": "AB-CD", "guid": "normalized"},
            {"name": "abcd", "guid": "exact"},
        ]
        result = svc.find_package(packages, "abcd")
        self.assertIsNotNone(result)
        self.assertEqual(result["guid"], "exact")


# ---------------------------------------------------------------------------
# ensure (integration of the full flow)
# ---------------------------------------------------------------------------


class TestEnsure(unittest.TestCase):
    def test_disabled_skips(self):
        svc, deps = _make_service()
        cfg = {"jellyfin_plugins": {"enabled": False}}
        svc.ensure(cfg, "/config", 60)
        deps.wait_for_service.assert_not_called()

    def test_missing_api_key_raises(self):
        svc, _ = _make_service(resolve_api_key=mock.Mock(return_value=""))
        cfg = {"jellyfin_plugins": {"enabled": True}}
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure(cfg, "/config", 60)
        self.assertIn("API key unavailable", str(ctx.exception))

    def test_empty_install_list_logs_warning(self):
        call_idx = [0]

        def fake_request(url, path, api_key, **kwargs):
            call_idx[0] += 1
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [], ""
            return 200, [], ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": []}}
        svc.ensure(cfg, "/config", 60)
        deps.log.assert_any_call("[WARN] Jellyfin plugins: enabled but install list is empty.")

    def test_plugin_already_installed_skips(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [{"Name": "OpenSubtitles"}], ""
            if path == "/Packages":
                return 200, [], ""
            return 200, None, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": ["OpenSubtitles"]}}
        svc.ensure(cfg, "/config", 60)
        deps.log.assert_any_call("[OK] Jellyfin plugins: already installed: OpenSubtitles")

    def test_plugin_install_success(self):
        installed_paths = []

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [{"name": "OpenSubtitles", "guid": "abc-123", "versions": []}], ""
            if path.startswith("/Packages/Installed/"):
                installed_paths.append(path)
                return 204, None, ""
            return 200, None, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": ["OpenSubtitles"]}}
        svc.ensure(cfg, "/config", 60)
        self.assertEqual(len(installed_paths), 1)
        self.assertIn("OpenSubtitles", installed_paths[0])
        self.assertIn("assemblyGuid=abc-123", installed_paths[0])

    def test_plugin_install_with_version_and_repo(self):
        installed_paths = []

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [
                    {"name": "OpenSubtitles", "guid": "abc-123", "versions": [
                        {"repositoryUrl": "https://repo.example.com"}
                    ]},
                ], ""
            if path.startswith("/Packages/Installed/"):
                installed_paths.append(path)
                return 204, None, ""
            return 200, None, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_plugins": {
                "enabled": True,
                "install": [
                    {
                        "name": "OpenSubtitles",
                        "version": "1.2.3",
                        "repository_url": "https://repo.example.com",
                    }
                ],
            }
        }
        svc.ensure(cfg, "/config", 60)
        self.assertEqual(len(installed_paths), 1)
        self.assertIn("version=1.2.3", installed_paths[0])
        self.assertIn("repositoryUrl=https", installed_paths[0])

    def test_plugin_not_found_optional_logs_warning(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [], ""
            return 200, None, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": ["MissingPlugin"]}}
        svc.ensure(cfg, "/config", 60)
        log_messages = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("package not found" in m for m in log_messages))

    def test_plugin_not_found_required_raises(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [], ""
            return 200, None, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_plugins": {
                "enabled": True,
                "install": [{"name": "MissingPlugin", "required": True}],
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure(cfg, "/config", 60)
        self.assertIn("package not found", str(ctx.exception))

    def test_plugin_install_failure_optional_logs_warning(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [{"name": "Failing", "guid": "x"}], ""
            if path.startswith("/Packages/Installed/"):
                return 500, None, "Internal error"
            return 200, None, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": ["Failing"]}}
        svc.ensure(cfg, "/config", 60)
        log_messages = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("failed to install" in m for m in log_messages))

    def test_plugin_install_failure_required_raises(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [{"name": "Failing", "guid": "x"}], ""
            if path.startswith("/Packages/Installed/"):
                return 500, None, "Internal error"
            return 200, None, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {
            "jellyfin_plugins": {
                "enabled": True,
                "install": [{"name": "Failing", "required": True}],
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure(cfg, "/config", 60)
        self.assertIn("failed to install", str(ctx.exception))

    def test_listing_installed_plugins_failure_raises(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 500, None, "Error"
            return 200, [], ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": ["x"]}}
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure(cfg, "/config", 60)
        self.assertIn("failed listing installed plugins", str(ctx.exception))

    def test_listing_packages_failure_raises(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 502, None, "Bad Gateway"
            return 200, [], ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": ["x"]}}
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure(cfg, "/config", 60)
        self.assertIn("failed listing available packages", str(ctx.exception))

    def test_reconcile_summary_log(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [{"Name": "Installed1"}], ""
            if path == "/Packages":
                return 200, [{"name": "NewPlugin", "guid": "g1"}], ""
            if path.startswith("/Packages/Installed/"):
                return 204, None, ""
            return 200, None, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": ["Installed1", "NewPlugin"]}}
        svc.ensure(cfg, "/config", 60)
        final_log = deps.log.call_args_list[-1][0][0]
        self.assertIn("install_requested=1", final_log)
        self.assertIn("already_installed=1", final_log)

    def test_string_entry_in_install_list(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [{"name": "SimplePlugin", "guid": "g1"}], ""
            if path.startswith("/Packages/Installed/"):
                return 204, None, ""
            return 200, None, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": ["SimplePlugin"]}}
        svc.ensure(cfg, "/config", 60)
        deps.log.assert_any_call("[OK] Jellyfin plugins: install requested for SimplePlugin")

    def test_empty_plugin_name_skipped(self):
        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [], ""
            return 200, None, ""

        svc, deps = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": [{"name": ""}, {"name": "  "}]}}
        svc.ensure(cfg, "/config", 60)
        final_log = deps.log.call_args_list[-1][0][0]
        self.assertIn("install_requested=0", final_log)

    def test_no_jellyfin_plugins_key_returns_early(self):
        svc, deps = _make_service()
        svc.ensure({}, "/config", 60)
        deps.wait_for_service.assert_not_called()

    def test_wait_for_service_called_with_correct_args(self):
        svc, _ = _make_service(resolve_api_key=mock.Mock(return_value=""))
        cfg = {"jellyfin_plugins": {"enabled": True, "url": "http://myhost:9090"}}
        with self.assertRaises(RuntimeError):
            svc.ensure(cfg, "/config", 120)
        svc.deps.wait_for_service.assert_called_once_with(
            "Jellyfin", "http://myhost:9090", "/System/Info/Public", 120,
        )

    def test_default_url_when_not_specified(self):
        svc, _ = _make_service(resolve_api_key=mock.Mock(return_value=""))
        cfg = {"jellyfin_plugins": {"enabled": True}}
        with self.assertRaises(RuntimeError):
            svc.ensure(cfg, "/config", 60)
        svc.deps.wait_for_service.assert_called_once_with(
            "Jellyfin", "http://jellyfin:8096", "/System/Info/Public", 60,
        )

    def test_package_guid_included_in_install_url(self):
        installed_paths = []

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [{"name": "Test", "guid": "myGuid"}], ""
            if path.startswith("/Packages/Installed/"):
                installed_paths.append(path)
                return 204, None, ""
            return 200, None, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": ["Test"]}}
        svc.ensure(cfg, "/config", 60)
        self.assertTrue(any("assemblyGuid=myGuid" in p for p in installed_paths))

    def test_package_name_url_encoded(self):
        installed_paths = []

        def fake_request(url, path, api_key, **kwargs):
            if path == "/Repositories" and kwargs.get("method") is None:
                return 200, [], ""
            if path == "/Plugins":
                return 200, [], ""
            if path == "/Packages":
                return 200, [{"name": "Name With Spaces", "guid": ""}], ""
            if path.startswith("/Packages/Installed/"):
                installed_paths.append(path)
                return 204, None, ""
            return 200, None, ""

        svc, _ = _make_service(jellyfin_request=fake_request)
        cfg = {"jellyfin_plugins": {"enabled": True, "install": ["Name With Spaces"]}}
        svc.ensure(cfg, "/config", 60)
        self.assertTrue(any("Name%20With%20Spaces" in p for p in installed_paths))


if __name__ == "__main__":
    unittest.main()
