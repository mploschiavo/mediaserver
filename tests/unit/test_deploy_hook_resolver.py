"""Tests for deploy_hook_config_resolver — hook configuration resolution."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.cli.workflows.deploy_hook_config_resolver as resolver_mod  # noqa: E402


class TestBootstrapJobHooks(unittest.TestCase):
    def test_empty_config(self):
        result = resolver_mod.bootstrap_job_hooks({})
        self.assertIsInstance(result, dict)

    def test_with_adapter_hooks(self):
        cfg = {"adapter_hooks": {"bootstrap_job": {"key": "val"}}}
        result = resolver_mod.bootstrap_job_hooks(cfg)
        self.assertIsInstance(result, dict)


class TestEdgeHooks(unittest.TestCase):
    def test_empty_config(self):
        result = resolver_mod.edge_hooks({})
        self.assertIsInstance(result, dict)

    def test_with_edge_config(self):
        cfg = {"adapter_hooks": {"edge": {"provider": "envoy"}}}
        result = resolver_mod.edge_hooks(cfg)
        self.assertIsInstance(result, dict)


class TestEdgeRouterProvider(unittest.TestCase):
    def test_default_provider(self):
        result = resolver_mod.edge_router_provider({})
        self.assertIsInstance(result, str)

    def test_with_edge_router(self):
        edge_cfg = {"edge_router": {"provider": "envoy"}}
        result = resolver_mod.edge_router_provider(edge_cfg)
        self.assertIsInstance(result, str)


class TestIngressClassPriority(unittest.TestCase):
    def test_empty_config(self):
        result = resolver_mod.ingress_class_priority({})
        self.assertIsInstance(result, tuple)

    def test_with_priority(self):
        edge_cfg = {"ingress_class_priority": ["envoy", "traefik"]}
        result = resolver_mod.ingress_class_priority(edge_cfg)
        self.assertEqual(result, ("envoy", "traefik"))


class TestEdgeRouterServiceNames(unittest.TestCase):
    def test_empty_config(self):
        result = resolver_mod.edge_router_service_names({})
        self.assertIsInstance(result, tuple)

    def test_with_config(self):
        edge_cfg = {"edge_router": {"service_names": ["envoy"]}}
        result = resolver_mod.edge_router_service_names(edge_cfg)
        self.assertIsInstance(result, tuple)


class TestRuntimeConfigPolicyHandlerSpec(unittest.TestCase):
    def test_empty_config(self):
        result = resolver_mod.runtime_config_policy_handler_spec({})
        self.assertIsInstance(result, str)


class TestRuntimeConfigPolicyParams(unittest.TestCase):
    def test_empty_config(self):
        result = resolver_mod.runtime_config_policy_params({})
        self.assertIsInstance(result, dict)


class TestComposePassthroughEnvVars(unittest.TestCase):
    def test_empty_config(self):
        result = resolver_mod.compose_passthrough_env_vars({})
        self.assertIsInstance(result, tuple)

    def test_with_vars(self):
        cfg = {"compose_passthrough_env_vars": ["VAR1", "VAR2"]}
        result = resolver_mod.compose_passthrough_env_vars(cfg)
        self.assertIsInstance(result, tuple)


class TestComposePrefightHandlers(unittest.TestCase):
    def test_empty_config(self):
        result = resolver_mod.compose_preflight_handlers({})
        self.assertIsInstance(result, tuple)


class TestMediaServerServiceNames(unittest.TestCase):
    def test_empty_config(self):
        result = resolver_mod.media_server_service_names({})
        self.assertIsInstance(result, tuple)


class TestAuthProviderMiddlewareDefaults(unittest.TestCase):
    def test_empty_config(self):
        result = resolver_mod.auth_provider_middleware_defaults({})
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
