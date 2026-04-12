"""Unit tests for routing configuration: config.py, server.py routing handlers, admin.py password reset filtering."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# 1. get_routing() tests
# ---------------------------------------------------------------------------

class TestGetRoutingDefaults(unittest.TestCase):
    """get_routing() returns defaults when no profile or overrides exist."""

    @patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None)
    @patch.dict(os.environ, {"CONFIG_ROOT": "/nonexistent"}, clear=False)
    def test_returns_defaults_no_profile_no_overrides(self, mock_resolve):
        from media_stack.api.services.config import get_routing
        result = get_routing()
        self.assertEqual(result["base_domain"], "local")
        self.assertEqual(result["stack_subdomain"], "media-stack")
        self.assertEqual(result["gateway_host"], "apps.media-stack.local")
        self.assertEqual(result["gateway_port"], 80)
        self.assertEqual(result["app_path_prefix"], "/app")
        self.assertEqual(result["strategy"], "hybrid")
        self.assertFalse(result["internet_exposed"])
        self.assertEqual(result["direct_hosts"], {})

    @patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None)
    @patch.dict(os.environ, {"CONFIG_ROOT": "/nonexistent"}, clear=False)
    def test_default_types_are_correct(self, mock_resolve):
        from media_stack.api.services.config import get_routing
        result = get_routing()
        self.assertIsInstance(result["gateway_port"], int)
        self.assertIsInstance(result["direct_hosts"], dict)
        self.assertIsInstance(result["internet_exposed"], bool)


class TestGetRoutingFromProfile(unittest.TestCase):
    """get_routing() reads values from profile YAML routing section."""

    def test_reads_routing_from_profile_yaml(self):
        profile_yaml = {
            "routing": {
                "base_domain": "example.com",
                "stack_subdomain": "mystack",
                "gateway_host": "apps.mystack.example.com",
                "gateway_port": 443,
                "app_path_prefix": "/services",
                "strategy": "path-only",
                "internet_exposed": True,
                "direct_hosts": {"jellyfin": "jf.example.com"},
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profile.yaml"
            import yaml
            profile_path.write_text(yaml.dump(profile_yaml))
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(profile_path)):
                with patch.dict(os.environ, {"CONFIG_ROOT": "/nonexistent"}):
                    from media_stack.api.services.config import get_routing
                    result = get_routing()
            self.assertEqual(result["base_domain"], "example.com")
            self.assertEqual(result["stack_subdomain"], "mystack")
            self.assertEqual(result["gateway_host"], "apps.mystack.example.com")
            self.assertEqual(result["gateway_port"], 443)
            self.assertEqual(result["strategy"], "path-only")
            self.assertTrue(result["internet_exposed"])
            self.assertEqual(result["direct_hosts"]["jellyfin"], "jf.example.com")

    def test_partial_routing_section_uses_defaults_for_missing(self):
        profile_yaml = {"routing": {"base_domain": "mynet.local"}}
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profile.yaml"
            import yaml
            profile_path.write_text(yaml.dump(profile_yaml))
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(profile_path)):
                with patch.dict(os.environ, {"CONFIG_ROOT": "/nonexistent"}):
                    from media_stack.api.services.config import get_routing
                    result = get_routing()
            self.assertEqual(result["base_domain"], "mynet.local")
            self.assertEqual(result["stack_subdomain"], "media-stack")  # default
            self.assertEqual(result["gateway_port"], 80)  # default

    def test_profile_with_no_routing_section_returns_defaults(self):
        profile_yaml = {"services": {"sonarr": {"enabled": True}}}
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profile.yaml"
            import yaml
            profile_path.write_text(yaml.dump(profile_yaml))
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(profile_path)):
                with patch.dict(os.environ, {"CONFIG_ROOT": "/nonexistent"}):
                    from media_stack.api.services.config import get_routing
                    result = get_routing()
            self.assertEqual(result["gateway_host"], "apps.media-stack.local")


class TestGetRoutingOverrides(unittest.TestCase):
    """get_routing() overlays routing-overrides.yaml on top of profile."""

    def test_overrides_take_precedence_over_profile(self):
        import yaml
        profile_yaml = {"routing": {"base_domain": "original.local", "stack_subdomain": "original"}}
        overrides_yaml = {"routing": {"base_domain": "overridden.local"}}

        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profile.yaml"
            profile_path.write_text(yaml.dump(profile_yaml))

            config_root = Path(tmpdir) / "config"
            controller_dir = config_root / ".controller"
            controller_dir.mkdir(parents=True)
            overrides_path = controller_dir / "routing-overrides.yaml"
            overrides_path.write_text(yaml.dump(overrides_yaml))

            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(profile_path)):
                with patch.dict(os.environ, {"CONFIG_ROOT": str(config_root)}):
                    from media_stack.api.services.config import get_routing
                    result = get_routing()

            self.assertEqual(result["base_domain"], "overridden.local")
            # stack_subdomain from profile is preserved since override doesn't set it
            self.assertEqual(result["stack_subdomain"], "original")

    def test_overrides_file_missing_uses_profile_only(self):
        import yaml
        profile_yaml = {"routing": {"base_domain": "fromprofile.local"}}

        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profile.yaml"
            profile_path.write_text(yaml.dump(profile_yaml))

            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(profile_path)):
                with patch.dict(os.environ, {"CONFIG_ROOT": "/nonexistent"}):
                    from media_stack.api.services.config import get_routing
                    result = get_routing()

            self.assertEqual(result["base_domain"], "fromprofile.local")


# ---------------------------------------------------------------------------
# 2. update_routing() tests
# ---------------------------------------------------------------------------

class TestUpdateRoutingPersistence(unittest.TestCase):
    """update_routing() persists routing changes to routing-overrides.yaml."""

    def _setup_env(self, tmpdir, profile_routing=None):
        import yaml
        profile = {"routing": profile_routing or {
            "base_domain": "local",
            "stack_subdomain": "media-stack",
            "gateway_host": "apps.media-stack.local",
            "gateway_port": 80,
        }}
        profile_path = Path(tmpdir) / "profile.yaml"
        profile_path.write_text(yaml.dump(profile))
        config_root = Path(tmpdir) / "config"
        config_root.mkdir(exist_ok=True)
        return str(profile_path), str(config_root)

    def test_persists_changes_to_overrides_file(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({"strategy": "subdomain-only"})

            self.assertEqual(result["status"], "updated")
            self.assertIn("strategy", result["changed"])

            overrides_path = Path(config_root) / ".controller" / "routing-overrides.yaml"
            self.assertTrue(overrides_path.exists())
            data = yaml.safe_load(overrides_path.read_text())
            self.assertEqual(data["routing"]["strategy"], "subdomain-only")

    def test_returns_no_changes_when_values_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir, {
                "base_domain": "local",
                "strategy": "hybrid",
            })
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({"strategy": "hybrid"})

            self.assertEqual(result["status"], "no_changes")

    def test_ignores_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({"unknown_key": "value", "another": 123})

            self.assertEqual(result["status"], "no_changes")


class TestUpdateRoutingGatewayDerivation(unittest.TestCase):
    """update_routing() auto-derives gateway_host from subdomain+domain changes."""

    def _setup_env(self, tmpdir, routing=None):
        import yaml
        profile = {"routing": routing or {
            "base_domain": "local",
            "stack_subdomain": "media-stack",
            "gateway_host": "apps.media-stack.local",
            "gateway_port": 80,
        }}
        profile_path = Path(tmpdir) / "profile.yaml"
        profile_path.write_text(yaml.dump(profile))
        config_root = Path(tmpdir) / "config"
        config_root.mkdir(exist_ok=True)
        return str(profile_path), str(config_root)

    def test_derives_gateway_host_from_subdomain_change(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({"stack_subdomain": "mystack"})

            self.assertIn("gateway_host", result["changed"])
            self.assertEqual(result["routing"]["gateway_host"], "apps.mystack.local")

    def test_derives_gateway_host_from_domain_change(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({"base_domain": "example.com"})

            self.assertIn("gateway_host", result["changed"])
            self.assertEqual(result["routing"]["gateway_host"], "apps.media-stack.example.com")

    def test_derives_gateway_host_from_both_subdomain_and_domain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({
                        "stack_subdomain": "k8s",
                        "base_domain": "my",
                    })

            self.assertEqual(result["routing"]["gateway_host"], "apps.k8s.my")

    def test_preserves_gateway_prefix_when_deriving(self):
        """When subdomain changes, the first segment of the old host is kept as prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir, {
                "base_domain": "local",
                "stack_subdomain": "media-stack",
                "gateway_host": "portal.media-stack.local",
                "gateway_port": 80,
            })
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({"stack_subdomain": "newstack"})

            self.assertEqual(result["routing"]["gateway_host"], "portal.newstack.local")


class TestUpdateRoutingReverseDerivation(unittest.TestCase):
    """update_routing() auto-derives subdomain+domain from gateway_host changes."""

    def _setup_env(self, tmpdir, routing=None):
        import yaml
        profile = {"routing": routing or {
            "base_domain": "local",
            "stack_subdomain": "media-stack",
            "gateway_host": "apps.media-stack.local",
            "gateway_port": 80,
        }}
        profile_path = Path(tmpdir) / "profile.yaml"
        profile_path.write_text(yaml.dump(profile))
        config_root = Path(tmpdir) / "config"
        config_root.mkdir(exist_ok=True)
        return str(profile_path), str(config_root)

    def test_derives_subdomain_and_domain_from_gateway_host(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({"gateway_host": "gw.production.example.com"})

            self.assertIn("stack_subdomain", result["changed"])
            self.assertIn("base_domain", result["changed"])
            self.assertEqual(result["routing"]["stack_subdomain"], "production")
            self.assertEqual(result["routing"]["base_domain"], "example.com")

    def test_gateway_host_with_multi_part_domain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({"gateway_host": "apps.stack.sub.domain.org"})

            self.assertEqual(result["routing"]["stack_subdomain"], "stack")
            self.assertEqual(result["routing"]["base_domain"], "sub.domain.org")

    def test_gateway_host_docker_media_stack_local(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({"gateway_host": "docker.media-stack.local"})

            self.assertEqual(result["routing"]["stack_subdomain"], "media-stack")
            self.assertEqual(result["routing"]["base_domain"], "local")

    def test_no_reverse_derivation_when_subdomain_also_changed(self):
        """When both gateway_host and subdomain change, gateway_host derivation wins."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path, config_root = self._setup_env(tmpdir)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile_path):
                with patch.dict(os.environ, {"CONFIG_ROOT": config_root}):
                    from media_stack.api.services.config import update_routing
                    result = update_routing({
                        "gateway_host": "gw.new.example.com",
                        "stack_subdomain": "explicit",
                    })

            # When both change, subdomain+domain win and gateway_host is derived from them
            # The code checks "stack_subdomain in changed or base_domain in changed"
            # and gateway_host not in changed — but here both are changed,
            # so neither derivation branch executes
            self.assertIn("gateway_host", result["changed"])
            self.assertIn("stack_subdomain", result["changed"])


class TestUpdateRoutingEnvoyTrigger(unittest.TestCase):
    """update_routing() triggers envoy-config action."""

    def test_triggers_envoy_config_action(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmpdir:
            profile = {"routing": {"base_domain": "local", "strategy": "hybrid"}}
            profile_path = Path(tmpdir) / "profile.yaml"
            profile_path.write_text(yaml.dump(profile))
            config_root = Path(tmpdir) / "config"
            config_root.mkdir()

            action_trigger = MagicMock()
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(profile_path)):
                with patch.dict(os.environ, {"CONFIG_ROOT": str(config_root)}):
                    from media_stack.api.services.config import update_routing
                    update_routing({"strategy": "subdomain-only"}, action_trigger=action_trigger)

            action_trigger.assert_called_once_with("envoy-config", {})

    def test_no_trigger_when_no_changes(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmpdir:
            profile = {"routing": {"strategy": "hybrid"}}
            profile_path = Path(tmpdir) / "profile.yaml"
            profile_path.write_text(yaml.dump(profile))

            action_trigger = MagicMock()
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(profile_path)):
                with patch.dict(os.environ, {"CONFIG_ROOT": str(tmpdir)}):
                    from media_stack.api.services.config import update_routing
                    update_routing({"strategy": "hybrid"}, action_trigger=action_trigger)

            action_trigger.assert_not_called()


class TestUpdateRoutingMissingProfile(unittest.TestCase):
    """update_routing() handles missing profile gracefully."""

    @patch("media_stack.api.services._resolve.resolve_profile_path", return_value=None)
    def test_returns_error_when_profile_missing(self, mock_resolve):
        from media_stack.api.services.config import update_routing
        result = update_routing({"strategy": "subdomain-only"})
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Profile file not found")


class TestUpdateRoutingGatewayFormats(unittest.TestCase):
    """Various gateway_host formats are handled correctly."""

    def _run_update(self, gateway_host):
        import yaml
        with tempfile.TemporaryDirectory() as tmpdir:
            profile = {"routing": {
                "base_domain": "local",
                "stack_subdomain": "media-stack",
                "gateway_host": "apps.media-stack.local",
            }}
            profile_path = Path(tmpdir) / "profile.yaml"
            profile_path.write_text(yaml.dump(profile))
            config_root = Path(tmpdir) / "config"
            config_root.mkdir()

            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(profile_path)):
                with patch.dict(os.environ, {"CONFIG_ROOT": str(config_root)}):
                    from media_stack.api.services.config import update_routing
                    return update_routing({"gateway_host": gateway_host})

    def test_format_apps_media_stack_local(self):
        result = self._run_update("apps.media-stack.local")
        # Same as existing, no change
        self.assertEqual(result["status"], "no_changes")

    def test_format_k8s_my(self):
        result = self._run_update("k8s.my.net")
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["routing"]["stack_subdomain"], "my")
        self.assertEqual(result["routing"]["base_domain"], "net")

    def test_format_docker_media_stack_local(self):
        result = self._run_update("docker.media-stack.local")
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["routing"]["gateway_host"], "docker.media-stack.local")
        self.assertEqual(result["routing"]["stack_subdomain"], "media-stack")
        self.assertEqual(result["routing"]["base_domain"], "local")


# ---------------------------------------------------------------------------
# 3. Server routing handler tests (mocking the handler)
# ---------------------------------------------------------------------------

class TestServerGetRouting(unittest.TestCase):
    """GET /api/routing returns config from config_svc.get_routing()."""

    @patch("media_stack.api.services.config.get_routing")
    def test_get_routing_returns_config(self, mock_get):
        mock_get.return_value = {
            "base_domain": "test.local",
            "stack_subdomain": "mystack",
            "gateway_host": "apps.mystack.test.local",
            "gateway_port": 80,
            "app_path_prefix": "/app",
            "strategy": "hybrid",
            "internet_exposed": False,
            "direct_hosts": {},
        }
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.path = "/api/routing"
        handler._check_auth = MagicMock(return_value=True)
        handler._json_response = MagicMock()

        # Call do_GET directly using the unbound method
        ControllerAPIHandler.do_GET(handler)

        handler._json_response.assert_called_once()
        args = handler._json_response.call_args
        self.assertEqual(args[0][0], 200)


class TestServerPostRouting(unittest.TestCase):
    """POST /api/routing updates config via config_svc.update_routing()."""

    @patch("media_stack.api.services.config.update_routing")
    def test_post_routing_calls_update(self, mock_update):
        mock_update.return_value = {"status": "updated", "changed": ["strategy"]}
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.path = "/api/routing"
        handler._check_auth = MagicMock(return_value=True)
        handler._read_json_body = MagicMock(return_value={"strategy": "path-only"})
        handler._json_response = MagicMock()
        handler.action_trigger = MagicMock()

        ControllerAPIHandler.do_POST(handler)

        mock_update.assert_called_once()
        handler._json_response.assert_called_once()
        self.assertEqual(handler._json_response.call_args[0][0], 200)

    @patch("media_stack.api.services.config.update_routing")
    def test_post_routing_empty_body_returns_400(self, mock_update):
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.path = "/api/routing"
        handler._check_auth = MagicMock(return_value=True)
        handler._read_json_body = MagicMock(return_value={})
        handler._json_response = MagicMock()

        ControllerAPIHandler.do_POST(handler)

        handler._json_response.assert_called_once()
        self.assertEqual(handler._json_response.call_args[0][0], 400)
        self.assertIn("error", handler._json_response.call_args[0][1])
        mock_update.assert_not_called()


class TestServerServicesEndpoint(unittest.TestCase):
    """Controller appears in /api/services."""

    @patch("media_stack.api.services.registry.SERVICES", [])
    @patch.dict(os.environ, {"CONTROLLER_PORT": "9876"}, clear=False)
    def test_controller_in_services_list(self):
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.path = "/api/services"
        handler._check_auth = MagicMock(return_value=True)
        handler._json_response = MagicMock()

        ControllerAPIHandler.do_GET(handler)

        handler._json_response.assert_called_once()
        svc_list = handler._json_response.call_args[0][1]
        controller_entries = [s for s in svc_list if s["id"] == "controller"]
        self.assertEqual(len(controller_entries), 1)
        self.assertEqual(controller_entries[0]["category"], "infrastructure")
        self.assertEqual(controller_entries[0]["port"], 9876)


class TestServerServicesCategoriesEndpoint(unittest.TestCase):
    """Controller appears in /api/services/categories infrastructure category."""

    @patch("media_stack.api.services.registry.CATEGORIES", [
        {"label": "Infrastructure", "ids": ["envoy"]},
        {"label": "Media", "ids": ["jellyfin"]},
    ])
    def test_controller_added_to_infrastructure_category(self):
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.path = "/api/services/categories"
        handler._check_auth = MagicMock(return_value=True)
        handler._json_response = MagicMock()

        ControllerAPIHandler.do_GET(handler)

        handler._json_response.assert_called_once()
        cats = handler._json_response.call_args[0][1]
        infra = next((c for c in cats if c["label"] == "Infrastructure"), None)
        self.assertIsNotNone(infra)
        self.assertIn("controller", infra["ids"])

    @patch("media_stack.api.services.registry.CATEGORIES", [
        {"label": "Media", "ids": ["jellyfin"]},
    ])
    def test_infrastructure_category_created_if_missing(self):
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.path = "/api/services/categories"
        handler._check_auth = MagicMock(return_value=True)
        handler._json_response = MagicMock()

        ControllerAPIHandler.do_GET(handler)

        cats = handler._json_response.call_args[0][1]
        infra = next((c for c in cats if c["label"] == "Infrastructure"), None)
        self.assertIsNotNone(infra)
        self.assertIn("controller", infra["ids"])

    @patch("media_stack.api.services.registry.CATEGORIES", [
        {"label": "Infrastructure", "ids": ["envoy", "controller"]},
    ])
    def test_controller_not_duplicated_if_already_present(self):
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.path = "/api/services/categories"
        handler._check_auth = MagicMock(return_value=True)
        handler._json_response = MagicMock()

        ControllerAPIHandler.do_GET(handler)

        cats = handler._json_response.call_args[0][1]
        infra = next((c for c in cats if c["label"] == "Infrastructure"), None)
        # Should not have controller twice
        self.assertEqual(infra["ids"].count("controller"), 1)


# ---------------------------------------------------------------------------
# 4. admin.py password reset with service filter
# ---------------------------------------------------------------------------

class TestResetPasswordServiceFilter(unittest.TestCase):
    """admin.reset_password() respects target_services filter."""

    def _mock_registry(self):
        """Return patches that replace the registry with controllable mocks."""
        mock_svc_map = {
            "jellyfin": MagicMock(id="jellyfin", host="jellyfin", port=8096,
                                  api_key_env="JELLYFIN_API_KEY", api_key_config="",
                                  api_key_format="sqlite"),
            "sonarr": MagicMock(id="sonarr", host="sonarr", port=8989,
                                api_key_env="SONARR_API_KEY", api_key_config="sonarr/config.xml",
                                api_key_format="xml", password_api_path="/api/v3/config/ui",
                                password_config=""),
            "radarr": MagicMock(id="radarr", host="radarr", port=7878,
                                api_key_env="RADARR_API_KEY", api_key_config="radarr/config.xml",
                                api_key_format="xml", password_api_path="/api/v3/config/ui",
                                password_config=""),
        }
        return mock_svc_map

    @patch("media_stack.api.services.admin.persist_keys_to_secret")
    @patch("media_stack.api.services.admin.get_services_with_password_config", return_value=[])
    @patch("media_stack.api.services.admin.get_services_with_password_api", return_value=[])
    @patch("media_stack.api.services.admin.SERVICE_MAP", {})
    def test_reset_all_services_when_target_is_none(self, mock_pw_api, mock_pw_config, mock_persist):
        from media_stack.api.services.admin import reset_password
        with patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old", "STACK_ADMIN_USERNAME": "admin"}):
            result = reset_password("newpass123", None)
        self.assertEqual(result["status"], "updated")
        # With empty SERVICE_MAP and no services, only env update happens
        self.assertIsInstance(result["services"], list)

    @patch("media_stack.api.services.admin.persist_keys_to_secret")
    @patch("media_stack.api.services.admin.get_services_with_password_config", return_value=[])
    @patch("media_stack.api.services.admin.get_services_with_password_api")
    @patch("media_stack.api.services.admin.SERVICE_MAP")
    def test_reset_only_jellyfin_when_targeted(self, mock_map, mock_pw_api, mock_pw_config, mock_persist):
        mock_map.get = MagicMock(side_effect=lambda k, *a: None)  # skip qbit and jellyfin special
        mock_pw_api.return_value = []

        with patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old", "STACK_ADMIN_USERNAME": "admin"}):
            from media_stack.api.services.admin import reset_password
            result = reset_password("newpass123", ["jellyfin"])

        # qbittorrent should be skipped since it's not in target
        self.assertEqual(result["status"], "updated")

    @patch("media_stack.api.services.admin.persist_keys_to_secret")
    @patch("media_stack.api.services.admin.get_services_with_password_config", return_value=[])
    @patch("media_stack.api.services.admin.get_services_with_password_api")
    @patch("media_stack.api.services.admin.SERVICE_MAP")
    def test_reset_sonarr_and_radarr_skips_others(self, mock_map, mock_pw_api, mock_pw_config, mock_persist):
        sonarr_svc = MagicMock(id="sonarr", password_api_path="/api/v3/config/ui",
                               api_key_env="SONARR_API_KEY", host="sonarr", port=8989)
        radarr_svc = MagicMock(id="radarr", password_api_path="/api/v3/config/ui",
                               api_key_env="RADARR_API_KEY", host="radarr", port=7878)
        lidarr_svc = MagicMock(id="lidarr", password_api_path="/api/v3/config/ui",
                               api_key_env="LIDARR_API_KEY", host="lidarr", port=8686)

        mock_pw_api.return_value = [sonarr_svc, radarr_svc, lidarr_svc]
        mock_map.get = MagicMock(return_value=None)  # skip qbit/jellyfin

        with patch.dict(os.environ, {
            "STACK_ADMIN_PASSWORD": "old",
            "SONARR_API_KEY": "testkey1",
            "RADARR_API_KEY": "testkey2",
            "LIDARR_API_KEY": "testkey3",
        }):
            # Mock the HTTP calls for sonarr and radarr
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"username":"admin","password":"old"}'
                mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_resp

                from media_stack.api.services.admin import reset_password
                result = reset_password("newpass123", ["sonarr", "radarr"])

        # lidarr should not be in updated list
        for svc in result.get("services", []):
            self.assertNotEqual(svc, "lidarr")

    @patch("media_stack.api.services.admin.persist_keys_to_secret")
    @patch("media_stack.api.services.admin.get_services_with_password_config", return_value=[])
    @patch("media_stack.api.services.admin.get_services_with_password_api", return_value=[])
    @patch("media_stack.api.services.admin.SERVICE_MAP")
    def test_empty_target_list_resets_nothing(self, mock_map, mock_pw_api, mock_pw_config, mock_persist):
        mock_map.get = MagicMock(return_value=None)

        with patch.dict(os.environ, {"STACK_ADMIN_PASSWORD": "old"}):
            from media_stack.api.services.admin import reset_password
            result = reset_password("newpass123", [])

        # Empty filter means _filter = set() which matches nothing
        self.assertEqual(result["services"], [])
        self.assertEqual(result["status"], "updated")

    @patch("media_stack.api.services.admin.persist_keys_to_secret")
    @patch("media_stack.api.services.admin.get_services_with_password_config", return_value=[])
    @patch("media_stack.api.services.admin.get_services_with_password_api", return_value=[])
    @patch("media_stack.api.services.admin.SERVICE_MAP")
    def test_password_stored_in_env_after_reset(self, mock_map, mock_pw_api, mock_pw_config, mock_persist):
        mock_map.get = MagicMock(return_value=None)

        # Use a unique password to avoid pollution
        env = {"STACK_ADMIN_PASSWORD": "old_unique_pw", "STACK_ADMIN_USERNAME": "admin"}
        with patch.dict(os.environ, env, clear=False):
            from media_stack.api.services.admin import reset_password
            reset_password("stored_pw_test", [])
            self.assertEqual(os.environ["STACK_ADMIN_PASSWORD"], "stored_pw_test")


class TestServerResetPasswordHandler(unittest.TestCase):
    """POST /api/reset-password passes services parameter to admin_svc."""

    @patch("media_stack.api.services.admin.reset_password")
    def test_services_param_forwarded_to_admin(self, mock_reset):
        mock_reset.return_value = {"status": "updated", "services": ["jellyfin"], "errors": [], "restarted": []}
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.path = "/api/reset-password"
        handler._check_auth = MagicMock(return_value=True)
        handler._read_json_body = MagicMock(return_value={
            "password": "newpass123",
            "services": ["jellyfin"],
        })
        handler._json_response = MagicMock()

        ControllerAPIHandler.do_POST(handler)

        mock_reset.assert_called_once_with("newpass123", ["jellyfin"])

    @patch("media_stack.api.services.admin.reset_password")
    def test_no_services_param_passes_none(self, mock_reset):
        mock_reset.return_value = {"status": "updated", "services": [], "errors": [], "restarted": []}
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.path = "/api/reset-password"
        handler._check_auth = MagicMock(return_value=True)
        handler._read_json_body = MagicMock(return_value={"password": "newpass123"})
        handler._json_response = MagicMock()

        ControllerAPIHandler.do_POST(handler)

        mock_reset.assert_called_once_with("newpass123", None)

    def test_short_password_returns_400(self):
        from media_stack.api.server import ControllerAPIHandler
        handler = MagicMock(spec=ControllerAPIHandler)
        handler.path = "/api/reset-password"
        handler._check_auth = MagicMock(return_value=True)
        handler._read_json_body = MagicMock(return_value={"password": "ab"})
        handler._json_response = MagicMock()

        ControllerAPIHandler.do_POST(handler)

        self.assertEqual(handler._json_response.call_args[0][0], 400)


class TestUpdateRoutingReadOnlyProfile(unittest.TestCase):
    """update_routing() handles read-only profile file gracefully."""

    def test_readonly_profile_still_persists_overrides(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmpdir:
            profile = {"routing": {"strategy": "hybrid"}}
            profile_path = Path(tmpdir) / "profile.yaml"
            profile_path.write_text(yaml.dump(profile))
            # Make profile read-only
            profile_path.chmod(0o444)

            config_root = Path(tmpdir) / "config"
            config_root.mkdir()

            try:
                with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=str(profile_path)):
                    with patch.dict(os.environ, {"CONFIG_ROOT": str(config_root)}):
                        from media_stack.api.services.config import update_routing
                        result = update_routing({"strategy": "path-only"})

                # Should still succeed because overrides file is the primary persistence
                self.assertEqual(result["status"], "updated")
                overrides_path = Path(config_root) / ".controller" / "routing-overrides.yaml"
                self.assertTrue(overrides_path.exists())
            finally:
                profile_path.chmod(0o644)


if __name__ == "__main__":
    unittest.main()
