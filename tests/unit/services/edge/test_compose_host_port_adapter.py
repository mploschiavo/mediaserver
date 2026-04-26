"""Tests for ComposeHostPortAdapter — pure-function checks on the
compose port-binding plan."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config.routing.schema_v2 import (  # noqa: E402
    Binding,
    ExposureConfig,
    RoutingConfigV2,
)
from media_stack.services.edge.compose_host_port_adapter import (  # noqa: E402
    ComposeHostPortAdapter,
)


def _cfg(enabled: bool, binding: Binding, gateway_port: int = 0) -> RoutingConfigV2:
    return RoutingConfigV2(
        gateway_host="m.example",
        gateway_port=gateway_port,
        exposure=ExposureConfig(enabled=enabled, binding=binding),
    )


class TestDetect(unittest.TestCase):
    def test_detect_false_when_kubernetes_env_set(self) -> None:
        os.environ["KUBERNETES_SERVICE_HOST"] = "1.2.3.4"
        try:
            self.assertFalse(ComposeHostPortAdapter().detect())
        finally:
            del os.environ["KUBERNETES_SERVICE_HOST"]

    def test_detect_requires_docker_sock(self) -> None:
        os.environ.pop("KUBERNETES_SERVICE_HOST", None)
        # Test environment is unlikely to have /var/run/docker.sock,
        # so this is a structural check; we only verify that the
        # function consults the path. Use a sentinel: monkey-patch
        # os.path.exists.
        adapter = ComposeHostPortAdapter()
        original = os.path.exists
        try:
            os.path.exists = lambda p: True if p == "/var/run/docker.sock" else original(p)  # type: ignore[assignment]
            self.assertTrue(adapter.detect())
            os.path.exists = lambda p: False if p == "/var/run/docker.sock" else original(p)  # type: ignore[assignment]
            self.assertFalse(adapter.detect())
        finally:
            os.path.exists = original  # type: ignore[assignment]


class TestPortsBinding(unittest.TestCase):
    def test_loopback_when_exposure_disabled(self) -> None:
        plan = ComposeHostPortAdapter().compute_apply_plan(
            _cfg(enabled=False, binding=Binding.COMPOSE_HOST_PORT),
        )
        rewrite = next(s for s in plan.steps if s.kind == "compose.rewrite")
        self.assertTrue(all(p.startswith("127.0.0.1:") for p in rewrite.payload["ports"]))

    def test_loopback_explicit_binding(self) -> None:
        plan = ComposeHostPortAdapter().compute_apply_plan(
            _cfg(enabled=True, binding=Binding.COMPOSE_LOOPBACK),
        )
        rewrite = next(s for s in plan.steps if s.kind == "compose.rewrite")
        self.assertTrue(all(p.startswith("127.0.0.1:") for p in rewrite.payload["ports"]))

    def test_public_bind_when_exposed(self) -> None:
        plan = ComposeHostPortAdapter().compute_apply_plan(
            _cfg(enabled=True, binding=Binding.COMPOSE_HOST_PORT),
        )
        rewrite = next(s for s in plan.steps if s.kind == "compose.rewrite")
        self.assertTrue(all(p.startswith("0.0.0.0:") for p in rewrite.payload["ports"]))

    def test_default_binds_80_and_443(self) -> None:
        plan = ComposeHostPortAdapter().compute_apply_plan(
            _cfg(enabled=True, binding=Binding.COMPOSE_HOST_PORT),
        )
        rewrite = next(s for s in plan.steps if s.kind == "compose.rewrite")
        ports = rewrite.payload["ports"]
        self.assertEqual(len(ports), 2)
        self.assertIn("0.0.0.0:80:8080", ports)
        self.assertIn("0.0.0.0:443:8443", ports)

    def test_custom_gateway_port_emits_single_port(self) -> None:
        plan = ComposeHostPortAdapter().compute_apply_plan(
            _cfg(enabled=True, binding=Binding.COMPOSE_HOST_PORT, gateway_port=8443),
        )
        rewrite = next(s for s in plan.steps if s.kind == "compose.rewrite")
        self.assertEqual(rewrite.payload["ports"], ["0.0.0.0:8443:8443"])


class TestComposeUpStep(unittest.TestCase):
    def test_compose_up_step_present(self) -> None:
        plan = ComposeHostPortAdapter().compute_apply_plan(
            _cfg(enabled=True, binding=Binding.COMPOSE_HOST_PORT),
        )
        kinds = [s.kind for s in plan.steps]
        self.assertIn("compose.up", kinds)


class TestWarnings(unittest.TestCase):
    def test_exposed_but_loopback_warns(self) -> None:
        plan = ComposeHostPortAdapter().compute_apply_plan(
            _cfg(enabled=True, binding=Binding.COMPOSE_LOOPBACK),
        )
        self.assertTrue(plan.warnings)
        self.assertIn("127.0.0.1", plan.warnings[0])


if __name__ == "__main__":
    unittest.main()
