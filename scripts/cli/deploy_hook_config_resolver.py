"""Helpers to resolve deploy/bootstrap hook config from bootstrap JSON."""

from __future__ import annotations

from typing import Any


def profile_actions(
    cfg: dict[str, object],
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
    dict[str, str],
    dict[str, tuple[str, ...]],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    adapter_hooks = cfg.get("adapter_hooks")
    if not isinstance(adapter_hooks, dict):
        return {}, {}, {}, {}, (), (), ()
    rebuild_hooks = adapter_hooks.get("rebuild")
    if not isinstance(rebuild_hooks, dict):
        return {}, {}, {}, {}, (), (), ()

    scale_to_zero: dict[str, tuple[str, ...]] = {}
    raw_scale_to_zero = rebuild_hooks.get("profile_scale_to_zero_apps")
    if raw_scale_to_zero is not None:
        if not isinstance(raw_scale_to_zero, dict):
            raise ValueError("adapter_hooks.rebuild.profile_scale_to_zero_apps must be an object")
        for profile, apps in raw_scale_to_zero.items():
            profile_key = str(profile or "").strip()
            if not profile_key:
                continue
            if not isinstance(apps, list):
                raise ValueError(
                    "adapter_hooks.rebuild.profile_scale_to_zero_apps."
                    f"{profile_key} must be an array"
                )
            resolved_apps = tuple(str(app or "").strip() for app in apps if str(app or "").strip())
            scale_to_zero[profile_key] = resolved_apps

    tls_hosts: dict[str, tuple[str, ...]] = {}
    tls_secret_names: dict[str, str] = {}
    raw_tls_profiles = rebuild_hooks.get("profile_tls")
    if raw_tls_profiles is not None:
        if not isinstance(raw_tls_profiles, dict):
            raise ValueError("adapter_hooks.rebuild.profile_tls must be an object")
        for profile, spec in raw_tls_profiles.items():
            profile_key = str(profile or "").strip()
            if not profile_key:
                continue
            if not isinstance(spec, dict):
                raise ValueError(
                    f"adapter_hooks.rebuild.profile_tls.{profile_key} must be an object"
                )
            raw_hosts = spec.get("hosts")
            if raw_hosts is not None:
                if not isinstance(raw_hosts, list):
                    raise ValueError(
                        f"adapter_hooks.rebuild.profile_tls.{profile_key}.hosts must be an array"
                    )
                hosts = tuple(
                    str(host or "").strip() for host in raw_hosts if str(host or "").strip()
                )
                tls_hosts[profile_key] = hosts
            secret_name = str(spec.get("secret_name") or "").strip()
            if secret_name:
                tls_secret_names[profile_key] = secret_name

    profile_manifest_paths: dict[str, tuple[str, ...]] = {}
    raw_profile_manifest_paths = rebuild_hooks.get("profile_manifest_paths")
    if raw_profile_manifest_paths is not None:
        if not isinstance(raw_profile_manifest_paths, dict):
            raise ValueError("adapter_hooks.rebuild.profile_manifest_paths must be an object")
        for profile, manifests in raw_profile_manifest_paths.items():
            profile_key = str(profile or "").strip()
            if not profile_key:
                continue
            if not isinstance(manifests, list):
                raise ValueError(
                    "adapter_hooks.rebuild.profile_manifest_paths."
                    f"{profile_key} must be an array"
                )
            profile_manifest_paths[profile_key] = tuple(
                str(item or "").strip() for item in manifests if str(item or "").strip()
            )

    component_enable_manifest_paths: tuple[str, ...] = ()
    raw_component_manifest_paths = rebuild_hooks.get("component_enable_manifest_paths")
    if raw_component_manifest_paths is not None:
        if not isinstance(raw_component_manifest_paths, list):
            raise ValueError(
                "adapter_hooks.rebuild.component_enable_manifest_paths must be an array"
            )
        component_enable_manifest_paths = tuple(
            str(item or "").strip()
            for item in raw_component_manifest_paths
            if str(item or "").strip()
        )

    preserve_secret_keys: tuple[str, ...] = ()
    raw_preserve_secret_keys = rebuild_hooks.get("preserve_secret_keys")
    if raw_preserve_secret_keys is not None:
        if not isinstance(raw_preserve_secret_keys, list):
            raise ValueError("adapter_hooks.rebuild.preserve_secret_keys must be an array")
        preserve_secret_keys = tuple(
            str(item or "").strip() for item in raw_preserve_secret_keys if str(item or "").strip()
        )

    base_manifest_paths: tuple[str, ...] = ()
    raw_base_manifest_paths = rebuild_hooks.get("base_manifest_paths")
    if raw_base_manifest_paths is not None:
        if not isinstance(raw_base_manifest_paths, list):
            raise ValueError("adapter_hooks.rebuild.base_manifest_paths must be an array")
        base_manifest_paths = tuple(
            str(item or "").strip() for item in raw_base_manifest_paths if str(item or "").strip()
        )

    return (
        scale_to_zero,
        tls_hosts,
        tls_secret_names,
        profile_manifest_paths,
        component_enable_manifest_paths,
        preserve_secret_keys,
        base_manifest_paths,
    )


def bootstrap_job_hooks(cfg: dict[str, object]) -> dict[str, object]:
    adapter_hooks = cfg.get("adapter_hooks")
    if not isinstance(adapter_hooks, dict):
        return {}
    bootstrap_job = adapter_hooks.get("bootstrap_job")
    if not isinstance(bootstrap_job, dict):
        return {}
    return bootstrap_job


def edge_hooks(cfg: dict[str, object]) -> dict[str, object]:
    adapter_hooks = cfg.get("adapter_hooks")
    if not isinstance(adapter_hooks, dict):
        return {}
    edge = adapter_hooks.get("edge")
    if not isinstance(edge, dict):
        return {}
    return edge


def ingress_class_priority(edge_cfg: dict[str, object]) -> tuple[str, ...]:
    raw = edge_cfg.get("ingress_class_priority")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item or "").strip() for item in raw if str(item or "").strip())


def edge_router_provider(edge_cfg: dict[str, object]) -> str:
    return str(edge_cfg.get("router_provider") or "").strip().lower()


def edge_router_service_names(edge_cfg: dict[str, object]) -> tuple[str, ...]:
    raw = edge_cfg.get("router_service_names")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item or "").strip() for item in raw if str(item or "").strip())


def edge_compose_provider_specs(
    edge_cfg: dict[str, object],
    defaults: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    specs: dict[str, dict[str, str]] = {provider: dict(spec) for provider, spec in defaults.items()}
    raw = edge_cfg.get("compose_provider_specs")
    if not isinstance(raw, dict):
        return specs
    for provider, spec in raw.items():
        provider_key = str(provider or "").strip().lower()
        if not provider_key or not isinstance(spec, dict):
            continue
        normalized: dict[str, str] = {}
        for key, value in spec.items():
            k = str(key or "").strip()
            v = str(value or "").strip()
            if k and v:
                normalized[k] = v
        if normalized:
            specs[provider_key] = normalized
    return specs


def media_server_service_names(edge_cfg: dict[str, object]) -> tuple[str, ...]:
    raw = edge_cfg.get("media_server_service_names")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item or "").strip() for item in raw if str(item or "").strip())


def auth_provider_middleware_defaults(edge_cfg: dict[str, object]) -> dict[str, str]:
    defaults: dict[str, str] = {}
    raw = edge_cfg.get("auth_provider_middleware_defaults")
    if not isinstance(raw, dict):
        return defaults
    for provider, middleware in raw.items():
        provider_key = str(provider or "").strip().lower()
        middleware_value = str(middleware or "").strip()
        if provider_key:
            defaults[provider_key] = middleware_value
    return defaults


def runtime_config_policy_handler_spec(bootstrap_job_cfg: dict[str, object]) -> str:
    return str(bootstrap_job_cfg.get("runtime_config_policy_handler") or "").strip()


def runtime_config_policy_params(bootstrap_job_cfg: dict[str, object]) -> dict[str, object]:
    raw = bootstrap_job_cfg.get("runtime_config_policy_params")
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def compose_passthrough_env_vars(bootstrap_job_cfg: dict[str, object]) -> tuple[str, ...]:
    raw = bootstrap_job_cfg.get("compose_passthrough_env_vars")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item or "").strip() for item in raw if str(item or "").strip())
