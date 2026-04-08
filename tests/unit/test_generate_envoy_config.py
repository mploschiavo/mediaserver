"""Unit tests for cli.generate_envoy_config_main profile loading."""

import tempfile
import unittest
from pathlib import Path

from media_stack.cli.commands.generate_envoy_config_main import _load_bootstrap_edge_hooks, _load_profile


class TestLoadProfile(unittest.TestCase):
    def test_loads_valid_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("routing:\n  gateway_host: test.local\n  strategy: hybrid\n")
            f.flush()
            result = _load_profile(f.name)
        self.assertEqual(result["routing"]["gateway_host"], "test.local")
        self.assertEqual(result["routing"]["strategy"], "hybrid")

    def test_returns_empty_for_missing(self):
        result = _load_profile("/nonexistent/profile.yaml")
        self.assertEqual(result, {})

    def test_returns_empty_for_none(self):
        result = _load_profile(None)
        self.assertEqual(result, {})


class TestLoadBootstrapEdgeHooks(unittest.TestCase):
    def test_loads_edge_hooks(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            f.write(json.dumps({
                "adapter_hooks": {
                    "edge": {
                        "router_provider": "envoy",
                        "path_prefix_preserve_service_names_by_provider": {
                            "envoy": ["sonarr", "radarr"],
                        },
                    }
                }
            }))
            f.flush()
            result = _load_bootstrap_edge_hooks(f.name)
        self.assertEqual(result["router_provider"], "envoy")
        self.assertEqual(
            result["path_prefix_preserve_service_names_by_provider"]["envoy"],
            ["sonarr", "radarr"],
        )

    def test_returns_empty_for_missing(self):
        result = _load_bootstrap_edge_hooks("/nonexistent/config.json")
        self.assertEqual(result, {})

    def test_returns_empty_for_none(self):
        result = _load_bootstrap_edge_hooks(None)
        self.assertEqual(result, {})


class TestCsv(unittest.TestCase):
    """Tests for _csv() — comma-separated string splitter."""

    def test_simple_split(self):
        from media_stack.cli.commands.generate_envoy_config_main import _csv

        result = _csv("sonarr,radarr,lidarr")
        self.assertEqual(result, ("sonarr", "radarr", "lidarr"))

    def test_strips_whitespace(self):
        from media_stack.cli.commands.generate_envoy_config_main import _csv

        result = _csv("  sonarr , radarr ,  lidarr  ")
        self.assertEqual(result, ("sonarr", "radarr", "lidarr"))

    def test_filters_empty_items(self):
        from media_stack.cli.commands.generate_envoy_config_main import _csv

        result = _csv("sonarr,,radarr,,,lidarr")
        self.assertEqual(result, ("sonarr", "radarr", "lidarr"))

    def test_whitespace_only_items_filtered(self):
        from media_stack.cli.commands.generate_envoy_config_main import _csv

        result = _csv("sonarr,  ,radarr, ,")
        self.assertEqual(result, ("sonarr", "radarr"))

    def test_empty_string_returns_empty_tuple(self):
        from media_stack.cli.commands.generate_envoy_config_main import _csv

        result = _csv("")
        self.assertEqual(result, ())

    def test_single_value(self):
        from media_stack.cli.commands.generate_envoy_config_main import _csv

        result = _csv("jellyfin")
        self.assertEqual(result, ("jellyfin",))

    def test_returns_tuple_not_list(self):
        from media_stack.cli.commands.generate_envoy_config_main import _csv

        result = _csv("a,b")
        self.assertIsInstance(result, tuple)


class TestBuildSyntheticServices(unittest.TestCase):
    """Tests for _build_synthetic_services() — K8s synthetic service builder."""

    def _get_default_specs(self) -> dict:
        """Return a compose_provider_specs dict with traefik-style keys."""
        return {
            "envoy": {
                "enable_label_key": "traefik.enable",
                "router_rule_key_template": "traefik.http.routers.{router_name}.rule",
                "router_service_key_template": "traefik.http.routers.{router_name}.service",
                "service_label_prefix": "traefik.http.services.",
            },
        }

    def test_returns_all_known_services(self):
        from media_stack.cli.commands.generate_envoy_config_main import (
            _build_synthetic_services,
            _DEFAULT_SERVICE_PORTS,
        )

        result = _build_synthetic_services("apps.test.local", self._get_default_specs())
        self.assertEqual(set(result.keys()), set(_DEFAULT_SERVICE_PORTS.keys()))

    def test_service_has_container_name(self):
        from media_stack.cli.commands.generate_envoy_config_main import _build_synthetic_services

        result = _build_synthetic_services("apps.test.local", self._get_default_specs())
        self.assertEqual(result["sonarr"]["container_name"], "sonarr")

    def test_service_has_labels_dict(self):
        from media_stack.cli.commands.generate_envoy_config_main import _build_synthetic_services

        result = _build_synthetic_services("apps.test.local", self._get_default_specs())
        self.assertIn("labels", result["sonarr"])
        self.assertIsInstance(result["sonarr"]["labels"], dict)

    def test_enable_label_set_to_true(self):
        from media_stack.cli.commands.generate_envoy_config_main import _build_synthetic_services

        result = _build_synthetic_services("apps.test.local", self._get_default_specs())
        labels = result["jellyfin"]["labels"]
        self.assertEqual(labels["traefik.enable"], "true")

    def test_router_rule_uses_service_name(self):
        from media_stack.cli.commands.generate_envoy_config_main import _build_synthetic_services

        result = _build_synthetic_services("apps.test.local", self._get_default_specs())
        labels = result["radarr"]["labels"]
        self.assertEqual(
            labels["traefik.http.routers.radarr.rule"],
            "Host(`radarr.local`)",
        )

    def test_router_service_label(self):
        from media_stack.cli.commands.generate_envoy_config_main import _build_synthetic_services

        result = _build_synthetic_services("apps.test.local", self._get_default_specs())
        labels = result["prowlarr"]["labels"]
        self.assertEqual(labels["traefik.http.routers.prowlarr.service"], "prowlarr")

    def test_port_label_matches_default(self):
        from media_stack.cli.commands.generate_envoy_config_main import (
            _build_synthetic_services,
            _DEFAULT_SERVICE_PORTS,
        )

        result = _build_synthetic_services("apps.test.local", self._get_default_specs())
        labels = result["jellyfin"]["labels"]
        port_key = "traefik.http.services.jellyfin.loadbalancer.server.port"
        self.assertEqual(labels[port_key], str(_DEFAULT_SERVICE_PORTS["jellyfin"]))

    def test_falls_back_to_traefik_spec_when_envoy_missing(self):
        from media_stack.cli.commands.generate_envoy_config_main import _build_synthetic_services

        specs_traefik_only = {
            "traefik": {
                "enable_label_key": "traefik.enable",
                "router_rule_key_template": "traefik.http.routers.{router_name}.rule",
                "router_service_key_template": "traefik.http.routers.{router_name}.service",
                "service_label_prefix": "traefik.http.services.",
            },
        }
        result = _build_synthetic_services("apps.test.local", specs_traefik_only)
        # Should still generate services using the traefik spec
        self.assertIn("sonarr", result)
        self.assertEqual(result["sonarr"]["labels"]["traefik.enable"], "true")

    def test_empty_specs_uses_hardcoded_defaults(self):
        from media_stack.cli.commands.generate_envoy_config_main import _build_synthetic_services

        result = _build_synthetic_services("apps.test.local", {})
        # When specs dict is empty, spec = {} and defaults kick in for key names
        labels = result["sonarr"]["labels"]
        # Default enable_key is "traefik.enable"
        self.assertEqual(labels["traefik.enable"], "true")

    def test_each_service_has_exactly_four_labels(self):
        from media_stack.cli.commands.generate_envoy_config_main import _build_synthetic_services

        result = _build_synthetic_services("apps.test.local", self._get_default_specs())
        for svc_name, svc in result.items():
            self.assertEqual(
                len(svc["labels"]),
                4,
                f"{svc_name} should have exactly 4 labels",
            )


if __name__ == "__main__":
    unittest.main()
