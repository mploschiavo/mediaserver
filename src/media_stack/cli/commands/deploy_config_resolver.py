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


def resolve_profile_actions(bootstrap_config: dict[str, object]) -> list[dict[str, object]]:
    """Resolve ordered deploy actions from the profile config."""
    return profile_actions(bootstrap_config)


def resolve_bootstrap_job_hooks(bootstrap_config: dict[str, object]) -> dict[str, object]:
    """Resolve bootstrap job hook configuration."""
    return bootstrap_job_hooks(bootstrap_config)


def resolve_edge_hooks(bootstrap_config: dict[str, object]) -> dict[str, object]:
    """Resolve edge routing hooks."""
    return edge_hooks(bootstrap_config)


def resolve_runtime_config_policy(bootstrap_config: dict[str, object]) -> str:
    """Resolve the runtime config policy handler spec."""
    return runtime_config_policy_handler_spec(bootstrap_config)


def resolve_runtime_config_params(bootstrap_config: dict[str, object]) -> dict[str, object]:
    """Resolve runtime config policy parameters."""
    return runtime_config_policy_params(bootstrap_config)


def resolve_compose_env_vars(bootstrap_config: dict[str, object]) -> tuple[str, ...]:
    """Resolve compose passthrough environment variables."""
    return compose_passthrough_env_vars(bootstrap_config)


def resolve_compose_preflights(bootstrap_config: dict[str, object]) -> tuple[str, ...]:
    """Resolve compose preflight handler specs."""
    return compose_preflight_handlers(bootstrap_config)


def resolve_edge_router_provider(hooks: dict[str, object]) -> str:
    """Determine the edge router provider from hooks config."""
    edge = hooks.get("edge") or {}
    provider = str((edge if isinstance(edge, dict) else {}).get("provider", "")).strip().lower()
    return provider or os.environ.get("EDGE_ROUTER_PROVIDER", "envoy").strip().lower()


def resolve_ingress_class_priority(hooks: dict[str, object]) -> tuple[str, ...]:
    """Resolve ingress class priority from hooks."""
    edge = hooks.get("edge") or {}
    raw = (edge if isinstance(edge, dict) else {}).get("ingress_class_priority")
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ("nginx", "public", "traefik")
