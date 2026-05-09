"""EdgeBindingAdapter — abstracts the deploy-mode-specific work of
making a routing config externally reachable.

The Envoy route_config (PR-2) is deploy-agnostic: same vhosts, same
clusters, same redirects. What differs across K8s and Compose is the
*binding*: how does a request from the public internet (or local
network) end up hitting Envoy's listener?

  * K8s: Ingress object + Service of type LoadBalancer/NodePort/
    ClusterIP, plus cert-manager Certificate resources for TLS.
  * Compose: ``services.envoy.ports:`` mapping in the compose file
    ``0.0.0.0:80`` vs ``127.0.0.1:80``, plus filesystem cert paths.

This module defines the Protocol both implementations must satisfy
plus a ``detect()`` function that picks the right one. PR-3 ships
the K8s adapter; PR-7 ships the Compose adapter.

The adapter is *pure* in the planning phase (``compute_apply_plan``)
— no I/O. The caller is responsible for executing the plan via
kubectl/docker. This keeps the adapter unit-testable without mocking
heavy external clients.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from media_stack.api.services.config.routing.schema_v2 import RoutingConfigV2


DeployMode = Literal["k8s", "compose", "auto"]


@dataclass
class ApplyPlanStep:
    """A single side-effecting action the caller must execute. The
    adapter doesn't apply anything itself — it produces a list of
    these so the caller can preview, log, or batch them."""
    kind: str                          # "ingress.apply" | "service.patch" | "cert.apply" | "compose.rewrite" | …
    description: str                   # human-readable summary for preview
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApplyPlan:
    steps: list[ApplyPlanStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.steps


class EdgeBindingAdapter(Protocol):
    """Contract every adapter satisfies. Implementations live in
    sibling modules (``k8s_ingress_adapter`` for PR-3, future
    ``compose_host_port_adapter`` for PR-7)."""

    @property
    def name(self) -> str: ...

    def detect(self) -> bool:
        """Return True if this adapter applies to the current runtime
        (e.g. K8s adapter checks for ``KUBERNETES_SERVICE_HOST``)."""
        ...

    def compute_apply_plan(self, cfg: RoutingConfigV2) -> ApplyPlan:
        """Pure: derive the side-effects required to bring the runtime
        into agreement with ``cfg``. No I/O — the caller executes the
        plan separately so tests stay deterministic."""
        ...


class EdgeBindingAdapterRegistry:
    """Registry-style helper for resolving the live edge-binding
    adapter.

    Kept as a class (rather than a free helper) so future precedence
    rules — e.g. environment-aware overrides, allow-listing — slot in
    as named methods alongside ``detect_active``.
    """

    def detect_active(
        self, adapters: list[EdgeBindingAdapter]
    ) -> EdgeBindingAdapter | None:
        """Return the first adapter whose ``detect()`` returns True. The
        order of ``adapters`` is the precedence (K8s before Compose since
        K8s is the more specific environment)."""
        for a in adapters:
            if a.detect():
                return a
        return None


_INSTANCE = EdgeBindingAdapterRegistry()
detect_active_adapter = _INSTANCE.detect_active
