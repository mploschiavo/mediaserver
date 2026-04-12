"""Deploy configuration resolution — extracted from deploy_stack_main.py.

Resolves profile-driven configuration: edge routing, auth providers,
service names, hook specifications, and runtime config policy.
"""

from __future__ import annotations

import os
from typing import Any

from media_stack.cli.workflows.deploy_hook_config_resolver import (
    bootstrap_job_hooks,
    compose_passthrough_env_vars,
    compose_preflight_handlers,
    edge_hooks,
    profile_actions,
    runtime_config_policy_handler_spec,
    runtime_config_policy_params,
)


class DeployConfigResolverService:
    """Wraps deploy configuration resolution functions."""

    def resolve_profile_actions(self, bootstrap_config: dict[str, object]) -> list[dict[str, object]]:
        """Resolve ordered deploy actions from the profile config."""
        return profile_actions(bootstrap_config)

    def resolve_bootstrap_job_hooks(self, bootstrap_config: dict[str, object]) -> dict[str, object]:
        """Resolve bootstrap job hook configuration."""
        return bootstrap_job_hooks(bootstrap_config)

    def resolve_edge_hooks(self, bootstrap_config: dict[str, object]) -> dict[str, object]:
        """Resolve edge routing hooks."""
        return edge_hooks(bootstrap_config)

    def resolve_runtime_config_policy(self, bootstrap_config: dict[str, object]) -> str:
        """Resolve the runtime config policy handler spec."""
        return runtime_config_policy_handler_spec(bootstrap_config)

    def resolve_runtime_config_params(self, bootstrap_config: dict[str, object]) -> dict[str, object]:
        """Resolve runtime config policy parameters."""
        return runtime_config_policy_params(bootstrap_config)

    def resolve_compose_env_vars(self, bootstrap_config: dict[str, object]) -> tuple[str, ...]:
        """Resolve compose passthrough environment variables."""
        return compose_passthrough_env_vars(bootstrap_config)

    def resolve_compose_preflights(self, bootstrap_config: dict[str, object]) -> tuple[str, ...]:
        """Resolve compose preflight handler specs."""
        return compose_preflight_handlers(bootstrap_config)

    def resolve_edge_router_provider(self, hooks: dict[str, object]) -> str:
        """Determine the edge router provider from hooks config."""
        edge = hooks.get("edge") or {}
        provider = str((edge if isinstance(edge, dict) else {}).get("provider", "")).strip().lower()
        return provider or os.environ.get("EDGE_ROUTER_PROVIDER", "envoy").strip().lower()

    def resolve_ingress_class_priority(self, hooks: dict[str, object]) -> tuple[str, ...]:
        """Resolve ingress class priority from hooks."""
        edge = hooks.get("edge") or {}
        raw = (edge if isinstance(edge, dict) else {}).get("ingress_class_priority")
        if isinstance(raw, (list, tuple)):
            return tuple(str(item).strip() for item in raw if str(item).strip())
        return ("nginx", "public", "traefik")


_instance = DeployConfigResolverService()
resolve_profile_actions = _instance.resolve_profile_actions
resolve_bootstrap_job_hooks = _instance.resolve_bootstrap_job_hooks
resolve_edge_hooks = _instance.resolve_edge_hooks
resolve_runtime_config_policy = _instance.resolve_runtime_config_policy
resolve_runtime_config_params = _instance.resolve_runtime_config_params
resolve_compose_env_vars = _instance.resolve_compose_env_vars
resolve_compose_preflights = _instance.resolve_compose_preflights
resolve_edge_router_provider = _instance.resolve_edge_router_provider
resolve_ingress_class_priority = _instance.resolve_ingress_class_priority
