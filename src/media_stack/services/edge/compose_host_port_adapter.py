"""ComposeHostPortAdapter — emits compose-file edits for the
``services.envoy`` port binding from a ``RoutingConfigV2``.

Pure-function adapter. The compose-file rewrite is described as a
plan; the caller does the actual file write + ``docker compose up -d
envoy``.

The compose-mode binding rule is straightforward:

  * exposure.enabled=False         → bind to 127.0.0.1 only
  * binding=compose_loopback       → bind to 127.0.0.1 only (alias)
  * binding=compose_host_port (or auto with exposure.enabled=True)
    → bind to 0.0.0.0 on host port 80/443

The adapter doesn't read the compose file itself; it emits a desired
``ports:`` list. The ``compose-config`` action in the controller
applies it.

This is the Compose half of the design §6 matrix. K8s lives in
``k8s_ingress_adapter.py``; both implement the
``EdgeBindingAdapter`` Protocol.
"""
from __future__ import annotations

import os
from typing import Any

from media_stack.api.services.config.routing.schema_v2 import (
    Binding,
    RoutingConfigV2,
)
from .binding_adapter import ApplyPlan, ApplyPlanStep


_DEFAULT_HTTP_PORT = 80
_DEFAULT_HTTPS_PORT = 443


def _bind_address(cfg: RoutingConfigV2) -> str:
    """Return ``0.0.0.0`` (publicly bound) or ``127.0.0.1`` (loopback)
    based on the exposure config."""
    if not cfg.exposure.enabled:
        return "127.0.0.1"
    if cfg.exposure.binding == Binding.COMPOSE_LOOPBACK:
        return "127.0.0.1"
    return "0.0.0.0"


def _ports_block(cfg: RoutingConfigV2) -> list[str]:
    """Build the compose ``ports:`` list. Format:
    ``"<bind>:<host_port>:<container_port>"``.

    Default ports 80/443 unless the operator pinned a different
    ``gateway_port`` (single-port workflows like dev environments).
    """
    bind = _bind_address(cfg)
    http = _DEFAULT_HTTP_PORT
    https = _DEFAULT_HTTPS_PORT
    # If gateway_port is non-standard (eg 8443), expose just that.
    if cfg.gateway_port and cfg.gateway_port not in (80, 443):
        return [f"{bind}:{cfg.gateway_port}:8443"]
    return [
        f"{bind}:{http}:8080",
        f"{bind}:{https}:8443",
    ]


class ComposeHostPortAdapter:
    """Compose deploy-mode binding adapter (matches K8sIngressAdapter
    on contract; see ``binding_adapter.py``)."""

    name = "compose_host_port"

    def detect(self) -> bool:
        # We're running under compose if the K8s env isn't set AND
        # the docker socket is mounted. The first half is the strong
        # signal; the second protects against running on a developer
        # laptop without docker, which would also lack
        # KUBERNETES_SERVICE_HOST.
        if os.environ.get("KUBERNETES_SERVICE_HOST"):
            return False
        return os.path.exists("/var/run/docker.sock")

    def compute_apply_plan(self, cfg: RoutingConfigV2) -> ApplyPlan:
        plan = ApplyPlan()
        ports = _ports_block(cfg)
        bind = _bind_address(cfg)

        plan.steps.append(ApplyPlanStep(
            kind="compose.rewrite",
            description=(
                f"Set services.envoy.ports → {ports} "
                f"(bind {bind}, exposed={cfg.exposure.enabled})"
            ),
            payload={
                "service": "envoy",
                "ports": ports,
                "bind_address": bind,
            },
        ))

        plan.steps.append(ApplyPlanStep(
            kind="compose.up",
            description="docker compose up -d envoy (apply the new port mapping)",
            payload={"service": "envoy"},
        ))

        if cfg.exposure.enabled and bind == "127.0.0.1":
            plan.warnings.append(
                "exposure.enabled=true but binding resolves to "
                "127.0.0.1 — public hostnames won't be reachable. "
                "Set binding=compose_host_port (or 'auto') to bind on 0.0.0.0.",
            )

        return plan
