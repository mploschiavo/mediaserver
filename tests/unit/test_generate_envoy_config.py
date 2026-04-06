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


if __name__ == "__main__":
    unittest.main()
