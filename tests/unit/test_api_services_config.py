"""Unit tests for media_stack.api.services.config and _resolve."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services._resolve import resolve_config_path, resolve_profile_path
from media_stack.api.services import config as MODULE


# ---------------------------------------------------------------------------
# resolve_config_path
# ---------------------------------------------------------------------------

class TestResolveConfigPath(unittest.TestCase):
    def test_returns_candidate_when_file_exists(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as f:
            result = resolve_config_path(f.name)
            self.assertEqual(result, f.name)

    def test_returns_none_when_candidate_missing(self):
        result = resolve_config_path("/no/such/file.json")
        self.assertIsNone(result)

    def test_falls_back_to_env_var(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as f:
            with patch.dict(os.environ, {"BOOTSTRAP_CONFIG_FILE": f.name}):
                result = resolve_config_path(None)
                self.assertEqual(result, f.name)

    def test_returns_none_when_nothing_found(self):
        with patch.dict(os.environ, {"BOOTSTRAP_CONFIG_FILE": ""}, clear=False):
            result = resolve_config_path("/nonexistent")
            self.assertIsNone(result)


# ---------------------------------------------------------------------------
# resolve_profile_path
# ---------------------------------------------------------------------------

class TestResolveProfilePath(unittest.TestCase):
    def test_returns_candidate_when_file_exists(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml") as f:
            result = resolve_profile_path(f.name)
            self.assertEqual(result, f.name)

    def test_returns_none_when_candidate_missing(self):
        result = resolve_profile_path("/no/such/profile.yaml")
        self.assertIsNone(result)

    def test_falls_back_to_env_var(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml") as f:
            with patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": f.name}):
                result = resolve_profile_path(None)
                self.assertEqual(result, f.name)


# ---------------------------------------------------------------------------
# get_profile
# ---------------------------------------------------------------------------

class TestGetProfile(unittest.TestCase):
    def test_valid_yaml(self):
        import yaml

        profile_data = {"routing": {"base_domain": "example.com"}, "apps": ["sonarr"]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(profile_data, f)
            f.flush()
            profile_path = f.name
        try:
            MODULE._invalidate_profile_cache()
            with patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": profile_path}):
                result = MODULE.get_profile()
                # routing is stripped from profile (has dedicated /api/routing endpoint)
                self.assertNotIn("routing", result["profile"])
                # non-stripped sections are present
                self.assertEqual(result["profile"]["apps"], ["sonarr"])
                self.assertEqual(result["file"], profile_path)
                self.assertNotIn("error", result)
        finally:
            MODULE._invalidate_profile_cache()
            os.unlink(profile_path)

    def test_missing_file(self):
        MODULE._invalidate_profile_cache()
        with patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": "/nonexistent.yaml"}):
            with mock.patch("media_stack.api.services._resolve._IMAGE_PROFILE", "/also/missing"):
                result = MODULE.get_profile()
                self.assertIsNone(result["profile"])
                self.assertIn("error", result)
        MODULE._invalidate_profile_cache()

    def test_invalid_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(":\n  bad:\n    - [unterminated\n")
            f.flush()
            bad_path = f.name
        try:
            MODULE._invalidate_profile_cache()
            with patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": bad_path}):
                result = MODULE.get_profile()
                # PyYAML may or may not error on this; the function catches exceptions
                # If it parsed (YAML is lenient), profile is a dict; if error, we get error key
                self.assertIn("profile", result)
        finally:
            MODULE._invalidate_profile_cache()
            os.unlink(bad_path)

    def test_empty_yaml_returns_empty_dict(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()
            empty_path = f.name
        try:
            MODULE._invalidate_profile_cache()
            with patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": empty_path}):
                result = MODULE.get_profile()
                self.assertEqual(result["profile"], {})
        finally:
            MODULE._invalidate_profile_cache()
            os.unlink(empty_path)


# ---------------------------------------------------------------------------
# save_profile
# ---------------------------------------------------------------------------

class TestSaveProfile(unittest.TestCase):
    def test_save_success(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("old content")
            f.flush()
            profile_path = f.name
        try:
            with patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": profile_path}):
                new_content = "routing:\n  base_domain: new.local\n"
                result = MODULE.save_profile(new_content)
                self.assertEqual(result["status"], "saved")
                self.assertEqual(Path(profile_path).read_text(), new_content)
        finally:
            os.unlink(profile_path)

    def test_save_calls_reload(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()
            profile_path = f.name
        try:
            callback = mock.MagicMock()
            with patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": profile_path}):
                MODULE.save_profile("content", reload_config=callback)
                callback.assert_called_once()
        finally:
            os.unlink(profile_path)

    def test_save_missing_profile(self):
        with patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": "/nonexistent.yaml"}):
            with mock.patch("media_stack.api.services._resolve._IMAGE_PROFILE", "/also/missing"):
                result = MODULE.save_profile("content")
                self.assertIn("error", result)


# ---------------------------------------------------------------------------
# get_routing
# ---------------------------------------------------------------------------

class TestGetRouting(unittest.TestCase):
    def test_profile_only(self):
        import yaml

        profile = {"routing": {"base_domain": "test.io", "gateway_port": 443}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(profile, f)
            f.flush()
            profile_path = f.name
        try:
            with patch.dict(os.environ, {
                "BOOTSTRAP_PROFILE_FILE": profile_path,
                "CONFIG_ROOT": "/tmp/nonexistent-config-root",
            }):
                result = MODULE.get_routing()
                self.assertEqual(result["base_domain"], "test.io")
                self.assertEqual(result["gateway_port"], 443)
                # Defaults for unset keys
                self.assertEqual(result["strategy"], "hybrid")
                self.assertFalse(result["internet_exposed"])
        finally:
            os.unlink(profile_path)

    def test_overrides_overlay(self):
        import yaml

        profile = {"routing": {"base_domain": "original.io", "strategy": "path-only"}}
        overrides = {"routing": {"base_domain": "override.io", "gateway_port": 8443}}

        tmpdir = tempfile.mkdtemp()
        profile_path = os.path.join(tmpdir, "profile.yaml")
        controller_dir = os.path.join(tmpdir, ".controller")
        os.makedirs(controller_dir)
        overrides_path = os.path.join(controller_dir, "routing-overrides.yaml")

        with open(profile_path, "w") as f:
            yaml.dump(profile, f)
        with open(overrides_path, "w") as f:
            yaml.dump(overrides, f)

        try:
            with patch.dict(os.environ, {
                "BOOTSTRAP_PROFILE_FILE": profile_path,
                "CONFIG_ROOT": tmpdir,
            }):
                result = MODULE.get_routing()
                # Override takes precedence
                self.assertEqual(result["base_domain"], "override.io")
                self.assertEqual(result["gateway_port"], 8443)
                # Profile value preserved where no override
                self.assertEqual(result["strategy"], "path-only")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_missing_profile_returns_defaults(self):
        with patch.dict(os.environ, {
            "BOOTSTRAP_PROFILE_FILE": "/nonexistent.yaml",
            "CONFIG_ROOT": "/tmp/nonexistent-config-root",
        }):
            with mock.patch("media_stack.api.services._resolve._IMAGE_PROFILE", "/also/missing"):
                result = MODULE.get_routing()
                self.assertEqual(result["base_domain"], "local")
                self.assertEqual(result["stack_subdomain"], "media-stack")
                self.assertEqual(result["gateway_host"], "apps.media-stack.local")
                self.assertEqual(result["gateway_port"], 80)


# ---------------------------------------------------------------------------
# update_routing
# ---------------------------------------------------------------------------

class TestUpdateRouting(unittest.TestCase):
    def _make_profile(self, profile_data: dict) -> tuple:
        """Create a temp profile + config root and return (profile_path, tmpdir)."""
        import yaml

        tmpdir = tempfile.mkdtemp()
        profile_path = os.path.join(tmpdir, "profile.yaml")
        with open(profile_path, "w") as f:
            yaml.dump(profile_data, f)
        return profile_path, tmpdir

    def test_valid_changes(self):
        profile_path, tmpdir = self._make_profile({"routing": {"base_domain": "old.io"}})
        try:
            with patch.dict(os.environ, {
                "BOOTSTRAP_PROFILE_FILE": profile_path,
                "CONFIG_ROOT": tmpdir,
            }):
                trigger = mock.MagicMock()
                result = MODULE.update_routing({"base_domain": "new.io"}, action_trigger=trigger)
                self.assertEqual(result["status"], "updated")
                self.assertIn("base_domain", result["changed"])
                # Two triggers: envoy-config (data-plane edge) and
                # ingress-config (K8s control-plane Ingress rules).
                # Ingress-config no-ops on compose but must fire on
                # both so the K8s clean-deploy path reconciles the
                # Ingress without dashboard click-through. (v1.0.162.)
                trigger.assert_any_call("envoy-config", {})
                trigger.assert_any_call("ingress-config", {})
                self.assertEqual(trigger.call_count, 2)
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_no_changes(self):
        profile_path, tmpdir = self._make_profile({"routing": {"base_domain": "same.io"}})
        try:
            with patch.dict(os.environ, {
                "BOOTSTRAP_PROFILE_FILE": profile_path,
                "CONFIG_ROOT": tmpdir,
            }):
                result = MODULE.update_routing({"base_domain": "same.io"})
                self.assertEqual(result["status"], "no_changes")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_gateway_host_auto_derived(self):
        """When base_domain or stack_subdomain changes without explicit gateway_host,
        gateway_host is auto-derived."""
        profile_path, tmpdir = self._make_profile({
            "routing": {
                "base_domain": "old.io",
                "stack_subdomain": "media",
                "gateway_host": "apps.media.old.io",
            }
        })
        try:
            with patch.dict(os.environ, {
                "BOOTSTRAP_PROFILE_FILE": profile_path,
                "CONFIG_ROOT": tmpdir,
            }):
                result = MODULE.update_routing({"base_domain": "new.io"})
                self.assertEqual(result["status"], "updated")
                self.assertIn("gateway_host", result["changed"])
                self.assertEqual(result["routing"]["gateway_host"], "apps.media.new.io")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_gateway_host_not_auto_derived_when_explicit(self):
        """When gateway_host is explicitly provided, it is NOT auto-derived."""
        profile_path, tmpdir = self._make_profile({
            "routing": {
                "base_domain": "old.io",
                "stack_subdomain": "media",
                "gateway_host": "apps.media.old.io",
            }
        })
        try:
            with patch.dict(os.environ, {
                "BOOTSTRAP_PROFILE_FILE": profile_path,
                "CONFIG_ROOT": tmpdir,
            }):
                result = MODULE.update_routing({
                    "base_domain": "new.io",
                    "gateway_host": "custom.new.io",
                })
                self.assertEqual(result["routing"]["gateway_host"], "custom.new.io")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_persists_overrides_file(self):
        import yaml

        profile_path, tmpdir = self._make_profile({"routing": {"strategy": "path-only"}})
        try:
            with patch.dict(os.environ, {
                "BOOTSTRAP_PROFILE_FILE": profile_path,
                "CONFIG_ROOT": tmpdir,
            }):
                MODULE.update_routing({"strategy": "hybrid"})
                overrides_path = Path(tmpdir) / ".controller" / "routing-overrides.yaml"
                self.assertTrue(overrides_path.is_file())
                overrides = yaml.safe_load(overrides_path.read_text())
                self.assertEqual(overrides["routing"]["strategy"], "hybrid")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_missing_profile_returns_error(self):
        with patch.dict(os.environ, {"BOOTSTRAP_PROFILE_FILE": "/nonexistent.yaml"}):
            with mock.patch("media_stack.api.services._resolve._IMAGE_PROFILE", "/also/missing"):
                result = MODULE.update_routing({"base_domain": "x.io"})
                self.assertIn("error", result)


# ---------------------------------------------------------------------------
# get_env
# ---------------------------------------------------------------------------

class TestGetEnv(unittest.TestCase):
    def test_compose_runtime(self):
        with patch.dict(os.environ, {
            "K8S_NAMESPACE": "",
            "NODE_IP": "192.168.1.100",
            "BOOTSTRAP_PROFILE_FILE": "",
        }, clear=False):
            result = MODULE.get_env()
            self.assertEqual(result["runtime"], "compose")
            self.assertEqual(result["node_ip"], "192.168.1.100")
            self.assertIn("python", result)

    def test_k8s_runtime(self):
        with patch.dict(os.environ, {
            "K8S_NAMESPACE": "media",
            "NODE_IP": "10.0.0.1",
            "BOOTSTRAP_PROFILE_FILE": "",
        }, clear=False):
            # Patch k8s import to avoid real cluster calls
            with mock.patch.dict("sys.modules", {"kubernetes": mock.MagicMock(), "kubernetes.client": mock.MagicMock(), "kubernetes.config": mock.MagicMock()}):
                result = MODULE.get_env()
                self.assertEqual(result["runtime"], "kubernetes")
                self.assertEqual(result["namespace"], "media")


# ---------------------------------------------------------------------------
# get_envvars / set_envvar
# ---------------------------------------------------------------------------

class TestEnvVars(unittest.TestCase):
    def test_get_envvars_filters_relevant(self):
        with patch.dict(os.environ, {
            "SONARR_API_KEY": "abc",
            "RADARR_API_KEY": "def",
            "UNRELATED_VAR": "xyz",
        }, clear=False):
            result = MODULE.get_envvars()
            self.assertIn("SONARR_API_KEY", result)
            self.assertNotIn("UNRELATED_VAR", result)

    def test_set_envvar(self):
        result = MODULE.set_envvar("TEST_BOOTSTRAP_KEY", "test_val")
        self.assertEqual(result["status"], "set")
        self.assertEqual(os.environ.get("TEST_BOOTSTRAP_KEY"), "test_val")
        # Cleanup
        os.environ.pop("TEST_BOOTSTRAP_KEY", None)


if __name__ == "__main__":
    unittest.main()
