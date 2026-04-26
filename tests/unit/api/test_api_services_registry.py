import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.registry import (  # noqa: E402
    ServiceDef,
    _load_registry,
    _parse_service_entry,
    reload_registry,
)

# Re-import module reference so we can patch its globals
import media_stack.api.services.registry as registry_mod  # noqa: E402


class TestServiceDefDataclass(unittest.TestCase):
    """Verify the ServiceDef frozen dataclass shape and defaults."""

    def test_minimal_construction(self):
        svc = ServiceDef(id="foo", name="Foo")
        self.assertEqual(svc.id, "foo")
        self.assertEqual(svc.name, "Foo")
        self.assertEqual(svc.desc, "")
        self.assertEqual(svc.category, "management")
        self.assertEqual(svc.host, "")
        self.assertEqual(svc.port, 0)
        self.assertEqual(svc.health_path, "/")
        self.assertEqual(svc.auth_path, "")
        self.assertEqual(svc.auth_mode, "X-Api-Key")
        self.assertEqual(svc.api_key_env, "")
        self.assertEqual(svc.api_key_config, "")
        self.assertEqual(svc.api_key_format, "")
        self.assertEqual(svc.version_path, "")
        self.assertEqual(svc.version_json_key, "")
        self.assertEqual(svc.password_api_path, "")
        self.assertEqual(svc.password_config, "")
        self.assertEqual(svc.profiles, [])
        self.assertTrue(svc.web_ui)
        self.assertFalse(svc.preserve_path_prefix)
        self.assertTrue(svc.scalable)
        self.assertFalse(svc.scale_to_zero)

    def test_frozen(self):
        svc = ServiceDef(id="bar", name="Bar")
        with self.assertRaises(AttributeError):
            svc.id = "changed"  # type: ignore[misc]


class TestParseServiceEntry(unittest.TestCase):
    """Unit tests for _parse_service_entry()."""

    def test_valid_minimal_entry(self):
        entry = {"id": "sonarr"}
        svc = _parse_service_entry(entry)
        self.assertIsNotNone(svc)
        self.assertEqual(svc.id, "sonarr")
        # name defaults to id when absent
        self.assertEqual(svc.name, "sonarr")
        # host defaults to id when absent
        self.assertEqual(svc.host, "sonarr")

    def test_valid_full_entry(self):
        entry = {
            "id": "radarr",
            "name": "Radarr",
            "desc": "Movie manager",
            "category": "media",
            "host": "radarr-svc",
            "port": 7878,
            "health_path": "/ping",
            "auth_path": "/api/v3/system/status",
            "auth_mode": "Bearer",
            "api_key_env": "RADARR_API_KEY",
            "api_key_config": "/config/config.xml",
            "api_key_format": "xml_ApiKey",
            "version_path": "/api/v3/system/status",
            "version_json_key": "version",
            "password_api_path": "",
            "password_config": "",
            "profiles": ["media", "arr"],
            "web_ui": True,
            "preserve_path_prefix": False,
            "scalable": True,
            "scale_to_zero": False,
        }
        svc = _parse_service_entry(entry)
        self.assertIsNotNone(svc)
        self.assertEqual(svc.id, "radarr")
        self.assertEqual(svc.name, "Radarr")
        self.assertEqual(svc.desc, "Movie manager")
        self.assertEqual(svc.category, "media")
        self.assertEqual(svc.host, "radarr-svc")
        self.assertEqual(svc.port, 7878)
        self.assertEqual(svc.health_path, "/ping")
        self.assertEqual(svc.auth_mode, "Bearer")
        self.assertEqual(svc.api_key_env, "RADARR_API_KEY")
        self.assertEqual(svc.profiles, ["media", "arr"])

    def test_returns_none_for_missing_id(self):
        self.assertIsNone(_parse_service_entry({"name": "NoId"}))

    def test_returns_none_for_empty_id(self):
        self.assertIsNone(_parse_service_entry({"id": ""}))

    def test_returns_none_for_non_dict(self):
        self.assertIsNone(_parse_service_entry("not-a-dict"))  # type: ignore[arg-type]
        self.assertIsNone(_parse_service_entry(42))  # type: ignore[arg-type]
        self.assertIsNone(_parse_service_entry(None))  # type: ignore[arg-type]

    def test_profiles_string_coerced_to_list(self):
        entry = {"id": "bazarr", "profiles": "subs"}
        svc = _parse_service_entry(entry)
        self.assertIsNotNone(svc)
        self.assertEqual(svc.profiles, ["subs"])

    def test_profiles_none_becomes_empty_list(self):
        entry = {"id": "jellyfin", "profiles": None}
        svc = _parse_service_entry(entry)
        self.assertIsNotNone(svc)
        self.assertEqual(svc.profiles, [])

    def test_profiles_list_preserved(self):
        entry = {"id": "prowlarr", "profiles": ["indexer", "arr"]}
        svc = _parse_service_entry(entry)
        self.assertEqual(svc.profiles, ["indexer", "arr"])

    def test_boolean_fields_default(self):
        entry = {"id": "test_svc"}
        svc = _parse_service_entry(entry)
        self.assertTrue(svc.web_ui)
        self.assertFalse(svc.preserve_path_prefix)
        self.assertTrue(svc.scalable)
        self.assertFalse(svc.scale_to_zero)

    def test_boolean_fields_override(self):
        entry = {
            "id": "test_svc",
            "web_ui": False,
            "preserve_path_prefix": True,
            "scalable": False,
            "scale_to_zero": True,
        }
        svc = _parse_service_entry(entry)
        self.assertFalse(svc.web_ui)
        self.assertTrue(svc.preserve_path_prefix)
        self.assertFalse(svc.scalable)
        self.assertTrue(svc.scale_to_zero)


class TestLoadRegistryPerServiceYaml(unittest.TestCase):
    """Test _load_registry() with per-service YAML directory strategy."""

    def test_loads_per_service_yaml_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc_dir = Path(tmpdir) / "services"
            svc_dir.mkdir()
            (svc_dir / "sonarr.yaml").write_text(textwrap.dedent("""\
                service:
                  id: sonarr
                  name: Sonarr
                  category: media
                  port: 8989
            """))
            (svc_dir / "radarr.yaml").write_text(textwrap.dedent("""\
                service:
                  id: radarr
                  name: Radarr
                  category: media
                  port: 7878
            """))

            with patch.dict(os.environ, {"SERVICES_REGISTRY_DIR": str(svc_dir)}):
                services, categories = _load_registry()

            ids = {s.id for s in services}
            self.assertIn("sonarr", ids)
            self.assertIn("radarr", ids)
            self.assertEqual(len(services), 2)

    def test_skips_underscore_prefixed_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc_dir = Path(tmpdir) / "services"
            svc_dir.mkdir()
            (svc_dir / "_template.yaml").write_text(textwrap.dedent("""\
                service:
                  id: template
                  name: Template
            """))
            (svc_dir / "jellyfin.yaml").write_text(textwrap.dedent("""\
                service:
                  id: jellyfin
                  name: Jellyfin
            """))

            with patch.dict(os.environ, {"SERVICES_REGISTRY_DIR": str(svc_dir)}):
                services, _ = _load_registry()

            ids = {s.id for s in services}
            self.assertNotIn("template", ids)
            self.assertIn("jellyfin", ids)

    def test_derives_categories_from_services(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc_dir = Path(tmpdir) / "services"
            svc_dir.mkdir()
            (svc_dir / "sonarr.yaml").write_text(textwrap.dedent("""\
                service:
                  id: sonarr
                  name: Sonarr
                  category: media
            """))
            (svc_dir / "homepage.yaml").write_text(textwrap.dedent("""\
                service:
                  id: homepage
                  name: Homepage
                  category: dashboard
            """))

            with patch.dict(os.environ, {"SERVICES_REGISTRY_DIR": str(svc_dir)}):
                services, categories = _load_registry()

            self.assertIn("media", categories)
            self.assertIn("dashboard", categories)

    def test_handles_bare_yaml_without_service_key(self):
        """If YAML has no 'service' wrapper, top-level dict is treated as entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc_dir = Path(tmpdir) / "services"
            svc_dir.mkdir()
            (svc_dir / "prowlarr.yaml").write_text(textwrap.dedent("""\
                id: prowlarr
                name: Prowlarr
                port: 9696
            """))

            with patch.dict(os.environ, {"SERVICES_REGISTRY_DIR": str(svc_dir)}):
                services, _ = _load_registry()

            self.assertEqual(len(services), 1)
            self.assertEqual(services[0].id, "prowlarr")
            self.assertEqual(services[0].port, 9696)

    def test_malformed_yaml_file_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc_dir = Path(tmpdir) / "services"
            svc_dir.mkdir()
            (svc_dir / "bad.yaml").write_text(": : : invalid yaml {{{{")
            (svc_dir / "good.yaml").write_text(textwrap.dedent("""\
                service:
                  id: good
                  name: Good
            """))

            with patch.dict(os.environ, {"SERVICES_REGISTRY_DIR": str(svc_dir)}):
                services, _ = _load_registry()

            ids = {s.id for s in services}
            self.assertIn("good", ids)


class TestLoadRegistryLegacyYaml(unittest.TestCase):
    """Test _load_registry() with the legacy single-file strategy."""

    def test_loads_legacy_services_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_file = Path(tmpdir) / "services.yaml"
            legacy_file.write_text(textwrap.dedent("""\
                categories:
                  - media
                  - management
                services:
                  - id: sonarr
                    name: Sonarr
                    category: media
                    port: 8989
                    api_key_env: SONARR_API_KEY
                  - id: prowlarr
                    name: Prowlarr
                    category: media
                    port: 9696
            """))

            with patch.dict(os.environ, {
                "SERVICES_REGISTRY_FILE": str(legacy_file),
                "SERVICES_REGISTRY_DIR": "",
            }), patch(
                f"{_load_registry.__module__}._find_services_dir", return_value=None
            ):
                services, categories = _load_registry()

            self.assertEqual(len(services), 2)
            self.assertEqual(categories, ["media", "management"])
            ids = {s.id for s in services}
            self.assertIn("sonarr", ids)
            self.assertIn("prowlarr", ids)

    def test_legacy_falls_back_when_dir_has_no_yaml(self):
        """Per-service dir exists but has no .yaml files -> falls back to legacy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_dir = Path(tmpdir) / "services"
            empty_dir.mkdir()

            legacy_file = Path(tmpdir) / "services.yaml"
            legacy_file.write_text(textwrap.dedent("""\
                services:
                  - id: jellyfin
                    name: Jellyfin
            """))

            with patch.dict(os.environ, {
                "SERVICES_REGISTRY_DIR": str(empty_dir),
                "SERVICES_REGISTRY_FILE": str(legacy_file),
            }), patch(
                f"{_load_registry.__module__}._find_services_dir", return_value=None
            ):
                services, _ = _load_registry()

            self.assertEqual(len(services), 1)
            self.assertEqual(services[0].id, "jellyfin")


class TestLookupHelpers(unittest.TestCase):
    """Test the module-level lookup helpers against controlled data."""

    @classmethod
    def setUpClass(cls):
        cls._services = [
            ServiceDef(id="sonarr", name="Sonarr", api_key_env="SONARR_KEY",
                       password_api_path="", password_config="",
                       profiles=[], web_ui=True, preserve_path_prefix=False,
                       scalable=True, scale_to_zero=False),
            ServiceDef(id="jellyfin", name="Jellyfin", api_key_env="",
                       password_api_path="/Users/Password",
                       password_config="",
                       profiles=["media"], web_ui=True,
                       preserve_path_prefix=True,
                       scalable=False, scale_to_zero=False),
            ServiceDef(id="bazarr", name="Bazarr", api_key_env="BAZARR_KEY",
                       password_api_path="",
                       password_config="/config/config.yaml",
                       profiles=[], web_ui=False,
                       preserve_path_prefix=False,
                       scalable=True, scale_to_zero=True),
        ]
        cls._service_map = {s.id: s for s in cls._services}

    def setUp(self):
        self._patch_services = patch.object(
            registry_mod, "SERVICES", self._services
        )
        self._patch_map = patch.object(
            registry_mod, "SERVICE_MAP", self._service_map
        )
        self._patch_services.start()
        self._patch_map.start()

    def tearDown(self):
        self._patch_services.stop()
        self._patch_map.stop()

    def test_get_service_found(self):
        svc = registry_mod.get_service("sonarr")
        self.assertIsNotNone(svc)
        self.assertEqual(svc.id, "sonarr")

    def test_get_service_not_found(self):
        self.assertIsNone(registry_mod.get_service("nonexistent"))

    def test_get_services_with_api_keys(self):
        result = registry_mod.get_services_with_api_keys()
        ids = {s.id for s in result}
        self.assertEqual(ids, {"sonarr", "bazarr"})

    def test_get_services_with_password_api(self):
        result = registry_mod.get_services_with_password_api()
        ids = {s.id for s in result}
        self.assertEqual(ids, {"jellyfin"})

    def test_get_services_with_password_config(self):
        result = registry_mod.get_services_with_password_config()
        ids = {s.id for s in result}
        self.assertEqual(ids, {"bazarr"})

    def test_get_active_service_ids(self):
        active = registry_mod.get_active_service_ids()
        # sonarr and bazarr have empty profiles -> always active
        # jellyfin has profiles=["media"] -> gated
        self.assertIn("sonarr", active)
        self.assertIn("bazarr", active)
        self.assertNotIn("jellyfin", active)

    def test_get_scalable_services(self):
        result = registry_mod.get_scalable_services()
        ids = {s.id for s in result}
        self.assertEqual(ids, {"sonarr", "bazarr"})

    def test_get_scale_to_zero_services(self):
        result = registry_mod.get_scale_to_zero_services()
        ids = {s.id for s in result}
        self.assertEqual(ids, {"bazarr"})

    def test_get_web_ui_services(self):
        result = registry_mod.get_web_ui_services()
        ids = {s.id for s in result}
        self.assertEqual(ids, {"sonarr", "jellyfin"})

    def test_get_preserve_path_prefix_services(self):
        result = registry_mod.get_preserve_path_prefix_services()
        ids = {s.id for s in result}
        self.assertEqual(ids, {"jellyfin"})


class TestReloadRegistry(unittest.TestCase):
    """Test that reload_registry() refreshes module globals."""

    def setUp(self):
        # Save original registry state so we can restore after each test.
        # reload_registry() rebinds module globals; without restoration
        # subsequent tests in the suite see a corrupted SERVICES list.
        self._orig_services = registry_mod.SERVICES
        self._orig_service_map = registry_mod.SERVICE_MAP
        self._orig_categories = list(registry_mod.CATEGORIES)
        self._orig_category_order = registry_mod._CATEGORY_ORDER

    def tearDown(self):
        registry_mod.SERVICES = self._orig_services
        registry_mod.SERVICE_MAP = self._orig_service_map
        registry_mod.CATEGORIES[:] = self._orig_categories
        registry_mod._CATEGORY_ORDER = self._orig_category_order

    def test_reload_refreshes_globals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc_dir = Path(tmpdir) / "services"
            svc_dir.mkdir()
            (svc_dir / "alpha.yaml").write_text(textwrap.dedent("""\
                service:
                  id: alpha
                  name: Alpha
                  category: tools
            """))

            with patch.dict(os.environ, {"SERVICES_REGISTRY_DIR": str(svc_dir)}):
                reload_registry()
                self.assertIn("alpha", registry_mod.SERVICE_MAP)
                self.assertEqual(registry_mod.SERVICE_MAP["alpha"].name, "Alpha")
                # CATEGORIES should contain the derived category
                cat_labels = [c["label"] for c in registry_mod.CATEGORIES]
                self.assertIn("Tools", cat_labels)

    def test_reload_clears_previous_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc_dir = Path(tmpdir) / "services"
            svc_dir.mkdir()
            (svc_dir / "beta.yaml").write_text(textwrap.dedent("""\
                service:
                  id: beta
                  name: Beta
            """))

            with patch.dict(os.environ, {"SERVICES_REGISTRY_DIR": str(svc_dir)}):
                reload_registry()
                self.assertIn("beta", registry_mod.SERVICE_MAP)

            # Now reload with an empty dir so beta should disappear
            with tempfile.TemporaryDirectory() as tmpdir2:
                empty_dir = Path(tmpdir2) / "services"
                empty_dir.mkdir()
                with patch.dict(os.environ, {
                    "SERVICES_REGISTRY_DIR": str(empty_dir),
                    "SERVICES_REGISTRY_FILE": "",
                }), patch(
                    f"{_load_registry.__module__}._find_services_dir", return_value=None
                ), patch(
                    f"{_load_registry.__module__}._find_services_yaml", return_value=None
                ):
                    reload_registry()
                    self.assertNotIn("beta", registry_mod.SERVICE_MAP)
                    self.assertEqual(registry_mod.CATEGORIES, [])


if __name__ == "__main__":
    unittest.main()
