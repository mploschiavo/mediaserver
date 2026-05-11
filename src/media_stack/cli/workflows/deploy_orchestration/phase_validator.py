"""DeployPhaseValidator — Validator for cfg + bootstrap-config inputs.

ADR-0015 Phase 4. Pre-Phase-4 ``_validate_inputs`` lived on
``RunnerPhasesMixin`` (a god-mixin in commands/) and ran ~70 LoC
of cross-cutting checks: file existence, namespace shape, profile
catalog membership, route-strategy / auth-provider / edge-provider
allow-lists, compose-platform provider bindings, runtime-policy
spec presence, runner image presence, disk / chaos / network
bounds.

Splitting into one SRP Validator gives the orchestrator a single
``validator.validate()`` call instead of an inline 70-LoC block.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

from media_stack.cli.workflows.deploy_errors import (
    DeployError,
    _MIN_STACK_DISK_ALLOCATION_GB,
)
from media_stack.core.edge.provider_registry import router_service_names_by_provider


if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )
    from media_stack.cli.workflows.deploy_config import DeployConfigService
    from media_stack.cli.workflows.deploy_orchestration.platform_adapter_factory import (
        PlatformAdapterFactory,
    )
    from media_stack.cli.workflows.deploy_orchestration.runtime_options import (
        DeployRuntimeOptions,
    )


class DeployPhaseValidator:
    """Validator: raise :class:`DeployError` for any invalid input.

    The validator runs once at the top of the pipeline. Every check
    is independent (no side-effects, no caching) — calling the
    validator a second time on the same cfg yields the same result.
    """

    def __init__(
        self,
        cfg: "DeployStackConfig",
        config_service: "DeployConfigService",
        platform_factory: "PlatformAdapterFactory",
        runtime_options: "DeployRuntimeOptions",
    ) -> None:
        self._cfg = cfg
        self._config_service = config_service
        self._platform_factory = platform_factory
        self._runtime_options = runtime_options

    def validate(self) -> None:
        cfg = self._cfg
        if not cfg.config_file.exists():
            raise DeployError(f"Config file not found: {cfg.config_file}")
        if not cfg.namespace.strip():
            raise DeployError("NAMESPACE cannot be empty.")
        platform_plugin = self._platform_factory.platform_plugin()
        cfg.ingress_domain = cfg.ingress_domain.lstrip(".").strip()
        if not cfg.ingress_domain:
            raise DeployError("INGRESS_DOMAIN cannot be empty.")
        if (
            platform_plugin.requires_dynamic_pvc_storage_mode
            and cfg.storage_mode != "dynamic-pvc"
        ):
            raise DeployError(
                f"Unsupported STORAGE_MODE '{cfg.storage_mode}'. "
                "legacy-hostpath was removed; use dynamic-pvc."
            )
        if cfg.profile not in {"minimal", "standard", "full", "public-demo", "power-user"}:
            raise DeployError(
                "Unknown PROFILE "
                f"'{cfg.profile}'. Supported: minimal, standard, full, public-demo, power-user."
            )
        valid_route_strategies = set(self._config_service.valid_route_strategies())
        if cfg.route_strategy not in valid_route_strategies:
            allowed = ", ".join(sorted(valid_route_strategies))
            raise DeployError(f"ROUTE_STRATEGY must be one of: {allowed}.")
        valid_auth_providers = set(self._config_service.valid_auth_providers())
        if cfg.auth_provider not in valid_auth_providers:
            allowed = ", ".join(sorted(valid_auth_providers))
            raise DeployError(f"AUTH_PROVIDER must be one of: {allowed}.")
        edge_router_provider = self._config_service.edge_router_provider()
        valid_edge_router_providers = set(self._config_service.valid_edge_router_providers())
        if edge_router_provider and edge_router_provider not in valid_edge_router_providers:
            allowed = ", ".join(sorted(valid_edge_router_providers))
            raise DeployError(
                "EDGE_ROUTER_PROVIDER (or adapter_hooks.edge.router_provider) "
                f"must be one of: {allowed}."
            )
        if (
            self._runtime_options.resolved_platform_target() == "compose"
            and edge_router_provider
            and edge_router_provider != "none"
        ):
            provider_spec = dict(
                self._config_service.edge_compose_provider_specs().get(edge_router_provider) or {}
            )
            builtin_provider_keys = set(router_service_names_by_provider().keys())
            if not provider_spec and edge_router_provider not in builtin_provider_keys:
                raise DeployError(
                    "Compose edge provider bindings are missing for "
                    f"'{edge_router_provider}'. "
                    "Define adapter_hooks.edge.compose_provider_specs.<provider> or "
                    "install a provider module under src/media_stack/core/edge/providers/<provider>/."
                )
        if platform_plugin.requires_runtime_config_policy_handler and cfg.run_bootstrap == "1":
            if not self._config_service.runtime_config_policy_handler_spec():
                raise DeployError(
                    "Compose bootstrap requires "
                    "adapter_hooks.bootstrap_job.runtime_config_policy_handler "
                    "in bootstrap config."
                )
        if not str(cfg.bootstrap_runner_image or "").strip():
            raise DeployError("BOOTSTRAP_RUNNER_IMAGE cannot be empty.")
        if cfg.disk_allocation_gb < _MIN_STACK_DISK_ALLOCATION_GB:
            raise DeployError(
                "STACK_DISK_ALLOCATION_GB must be at least " f"{_MIN_STACK_DISK_ALLOCATION_GB}."
            )
        if cfg.chaos_duration_minutes < 1 or cfg.chaos_duration_minutes > 120:
            raise DeployError("CHAOS_DURATION_MINUTES must be between 1 and 120.")
        if cfg.chaos_interval_seconds < 0 or cfg.chaos_interval_seconds > 3600:
            raise DeployError("CHAOS_INTERVAL_SECONDS must be between 0 and 3600.")
        if (
            self._runtime_options.is_truthy(cfg.chaos_enabled)
            and not self._runtime_options.chaos_actions()
        ):
            raise DeployError(
                "CHAOS_ACTIONS must include at least one action when chaos is enabled."
            )
        try:
            network = ipaddress.ip_network(cfg.network_cidr, strict=False)
        except ValueError as exc:
            raise DeployError(f"Invalid STACK_NETWORK_CIDR '{cfg.network_cidr}'.") from exc
        if not network.is_private:
            raise DeployError("STACK_NETWORK_CIDR must be private (10/8, 172.16/12, 192.168/16).")


__all__ = ["DeployPhaseValidator"]
