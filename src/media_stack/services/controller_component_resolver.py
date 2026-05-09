"""Resolve active bootstrap components from config + plugin manifests."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_stack.services.plugin_manifest_loader import load_plugin_manifests
from media_stack.services.top_level_config_model import TopLevelBootstrapConfig
from media_stack.core.exceptions import ConfigError
import logging


def normalize_technology_token(value: Any) -> str:
    token = str(value or "").strip().lower()
    token = re.sub(r"[^a-z0-9]+", "-", token)
    return token.strip("-")


def canonicalize_technology(value: Any, aliases: dict[str, str]) -> str:
    token = normalize_technology_token(value)
    if not token:
        return ""
    return aliases.get(token, token)


def _coerce_technology_list(value: Any, aliases: dict[str, str]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        token = canonicalize_technology(item, aliases)
        if token and token not in out:
            out.append(token)
    return tuple(out)


def _adapter_hooks(cfg: dict[str, Any]) -> dict[str, Any]:
    hooks = cfg.get("adapter_hooks")
    if isinstance(hooks, dict):
        return dict(hooks)
    return {}


def _phase_plan_steps(
    value: Any,
    *,
    pipeline: str,
) -> tuple[ControllerPhasePlanStep, ...]:
    if not isinstance(value, list):
        return ()
    out: list[ControllerPhasePlanStep] = []
    for index, item in enumerate(value):
        operation = ""
        skip_flag = ""
        phase_name = ""
        enabled = True
        when: Any = True
        params: dict[str, Any] = {}
        if isinstance(item, str):
            operation = str(item).strip()
            if not operation:
                raise ConfigError(
                    f"adapter_hooks.{pipeline}.phase_plan[{index}] must be a non-empty operation string."
                )
        elif isinstance(item, dict):
            operation = str(item.get("operation") or "").strip()
            if not operation:
                raise ConfigError(
                    f"adapter_hooks.{pipeline}.phase_plan[{index}].operation must be a non-empty string."
                )
            skip_flag = normalize_flag_token(item.get("skip_flag"))
            phase_name = str(item.get("phase_name") or "").strip()
            if "enabled" in item:
                enabled = bool(item.get("enabled"))
            if "when" in item:
                when = item.get("when")
            raw_params = item.get("params")
            if raw_params is not None and not isinstance(raw_params, dict):
                raise ConfigError(
                    f"adapter_hooks.{pipeline}.phase_plan[{index}].params must be an object/map."
                )
            if isinstance(raw_params, dict):
                params = dict(raw_params)
        else:
            raise ConfigError(
                f"adapter_hooks.{pipeline}.phase_plan[{index}] must be a string or object."
            )
        out.append(
            ControllerPhasePlanStep(
                operation=operation,
                skip_flag=skip_flag,
                phase_name=phase_name,
                enabled=enabled,
                when=when,
                params=params,
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class ManifestCatalog:
    aliases: dict[str, str] = field(default_factory=dict)
    technologies: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ControllerComponentPlan:
    config: dict[str, Any]
    aliases: dict[str, str]
    role_bindings: dict[str, str]
    managed_apps: tuple[str, ...]
    scale_to_zero_apps: tuple[str, ...]
    technology_settings: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ControllerPhasePlanStep:
    operation: str
    skip_flag: str = ""
    phase_name: str = ""
    enabled: bool = True
    when: Any = True
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PhaseSkipFlagSpec:
    key: str
    option_strings: tuple[str, ...]
    env_vars: tuple[str, ...]
    help: str


def normalize_flag_token(value: Any) -> str:
    token = str(value or "").strip().lower()
    token = re.sub(r"[^a-z0-9]+", "_", token)
    token = token.strip("_")
    return token


def resolve_pipeline_phase_plan(
    cfg: dict[str, Any],
    *,
    pipeline: str,
    allow_empty: bool = False,
) -> tuple[ControllerPhasePlanStep, ...]:
    hooks = _adapter_hooks(cfg)
    section = hooks.get(pipeline)
    if not isinstance(section, dict):
        if allow_empty:
            return ()
        raise ConfigError(
            f"adapter_hooks.{pipeline} must be defined as an object with a phase_plan list."
        )

    plan = _phase_plan_steps(section.get("phase_plan"), pipeline=pipeline)
    if plan:
        return plan
    if allow_empty:
        return ()
    raise ConfigError(
        f"adapter_hooks.{pipeline}.phase_plan must declare at least one phase operation."
    )


def _resolve_skip_flag_aliases(cfg: dict[str, Any]) -> dict[str, dict[str, tuple[str, ...]]]:
    """Load legacy skip flag aliases from config.json (backward compat only).

    Generic skip flags (--skip-torrent-client-ensure, SKIP_TORRENT_CLIENT_ENSURE)
    are auto-generated from phase_plan skip_flag keys — no aliases needed.
    """
    hooks = _adapter_hooks(cfg)
    raw = hooks.get("skip_flag_aliases")
    if not isinstance(raw, dict):
        return {}

    aliases: dict[str, dict[str, tuple[str, ...]]] = {}
    for key, value in raw.items():
        token = normalize_flag_token(key)
        if not token or not isinstance(value, dict):
            continue
        options = tuple(
            str(o).strip() for o in (value.get("options") or ()) if str(o).strip()
        )
        env_vars = tuple(
            str(e).strip() for e in (value.get("env_vars") or ()) if str(e).strip()
        )
        aliases[token] = {"options": options, "env_vars": env_vars}
    return aliases


def _merge_platform_adapter_hooks(payload: dict[str, Any], config_dir: Path) -> dict[str, Any]:
    """Merge platform-specific adapter hooks from YAML (e.g. adapter-hooks.k8s.yaml)."""
    import os
    try:
        import yaml
    except ImportError:
        return payload

    platform = os.environ.get("MEDIA_STACK_PLATFORM", "").strip().lower()
    if not platform:
        for pf in [Path(os.environ.get("BOOTSTRAP_PROFILE_FILE", "").strip() or "/dev/null"),
                    Path("/opt/media-stack/contracts/media-stack.profile.yaml"),
                    config_dir / "media-stack.profile.yaml"]:
            if pf.is_file():
                try:
                    profile = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
                    platform = str(
                        (profile.get("metadata") or {}).get("platform", "")
                    ).strip().lower()
                    if platform:
                        break
                except Exception as exc:
                    log_swallowed(exc)
    if not platform:
        return payload

    filename = f"adapter-hooks.{platform}.yaml"
    for candidate_dir in [config_dir, Path("/opt/media-stack/contracts")]:
        hooks_file = candidate_dir / filename
        if hooks_file.is_file():
            try:
                platform_hooks = yaml.safe_load(hooks_file.read_text(encoding="utf-8")) or {}
                if isinstance(platform_hooks, dict):
                    existing = payload.get("adapter_hooks")
                    if not isinstance(existing, dict):
                        existing = {}
                    merged = dict(existing)
                    for key, value in platform_hooks.items():
                        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                            combined = dict(merged[key])
                            combined.update(value)
                            merged[key] = combined
                        else:
                            merged[key] = value
                    payload = dict(payload)
                    payload["adapter_hooks"] = merged
            except Exception as exc:
                log_swallowed(exc)
            break
    return payload


def load_bootstrap_config(config_file: Path) -> dict[str, Any]:
    path = Path(config_file)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid JSON in config file {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigError("Bootstrap config root must be an object.")
    else:
        payload = {}
    payload = _merge_platform_adapter_hooks(payload, path.parent)
    try:
        return TopLevelBootstrapConfig.from_dict(payload).to_dict()
    except ValueError as exc:
        raise ConfigError(f"Invalid bootstrap config: {exc}") from exc


def build_manifest_catalog() -> ManifestCatalog:
    aliases: dict[str, str] = {}
    technologies: list[str] = []

    for manifest in load_plugin_manifests():
        technology = normalize_technology_token(manifest.technology)
        if not technology:
            continue
        aliases[technology] = technology
        if technology not in technologies:
            technologies.append(technology)
        for alias in manifest.aliases:
            token = normalize_technology_token(alias)
            if token and token not in aliases:
                aliases[token] = technology

    return ManifestCatalog(
        aliases=aliases,
        technologies=tuple(technologies),
    )


def resolve_role_bindings(
    cfg: dict[str, Any],
    *,
    aliases: dict[str, str],
) -> dict[str, str]:
    bindings = cfg.get("technology_bindings")
    if not isinstance(bindings, dict):
        bindings = {}

    resolved: dict[str, str] = {}
    for role_key, value in bindings.items():
        key = str(role_key or "").strip()
        if not key:
            continue
        resolved[key] = canonicalize_technology(value, aliases)

    return resolved


def resolve_technology_settings(
    cfg: dict[str, Any],
    *,
    aliases: dict[str, str],
    technologies: tuple[str, ...] = (),
    role_bindings: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    discovered_tokens: list[str] = []
    for token in technologies:
        value = canonicalize_technology(token, aliases)
        if value and value not in discovered_tokens:
            discovered_tokens.append(value)
    for value in (role_bindings or {}).values():
        token = canonicalize_technology(value, aliases)
        if token and token not in discovered_tokens:
            discovered_tokens.append(token)

    out: dict[str, dict[str, Any]] = {}
    raw_clients = cfg.get("download_clients")
    if isinstance(raw_clients, dict):
        for key, value in raw_clients.items():
            token = canonicalize_technology(key, aliases)
            if token and token not in discovered_tokens:
                discovered_tokens.append(token)
            if token and isinstance(value, dict):
                out[token] = dict(value)

    for token in discovered_tokens:
        section = cfg.get(token)
        if not isinstance(section, dict):
            continue
        merged = dict(out.get(token) or {})
        merged.update(dict(section))
        out[token] = merged

    return out


def resolve_runner_phase_script(
    cfg: dict[str, Any],
    *,
    phase_key: str,
    technology: str,
    aliases: dict[str, str] | None = None,
) -> str:
    normalized_aliases = dict(aliases or {})
    raw_tech = normalize_technology_token(technology)
    canonical = canonicalize_technology(raw_tech, normalized_aliases)

    # 1. Load from per-service YAML plugin.phase_scripts
    try:
        from media_stack.core.service_registry.registry import _find_services_dir
        import yaml as _yaml
        svc_dir = _find_services_dir()
        if svc_dir:
            for tech_id in (canonical, raw_tech):
                if not tech_id:
                    continue
                yaml_file = svc_dir / f"{tech_id}.yaml"
                if yaml_file.is_file():
                    try:
                        data = _yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
                        phase_scripts = (data.get("plugin") or {}).get("phase_scripts")
                        if isinstance(phase_scripts, dict):
                            result = str(phase_scripts.get(phase_key) or "").strip()
                            if result:
                                return result
                    except Exception as exc:
                        log_swallowed(exc)
    except Exception as exc:
        log_swallowed(exc)

    # 2. Fall back to config.json runner_phase_scripts
    hooks = _adapter_hooks(cfg)
    mappings = hooks.get("runner_phase_scripts")
    if isinstance(mappings, dict):
        phase_map = mappings.get(phase_key)
        if isinstance(phase_map, dict):
            candidates: list[str] = []
            for token in (raw_tech, canonical, "*"):
                if token and token not in candidates:
                    candidates.append(token)
            for token in candidates:
                candidate = str(phase_map.get(token) or "").strip()
                if candidate:
                    return candidate
    return ""


def resolve_bootstrap_enable_components(
    cfg: dict[str, Any],
    *,
    aliases: dict[str, str],
) -> tuple[str, ...]:
    hooks = _adapter_hooks(cfg)
    bootstrap_all = hooks.get("bootstrap_all")
    if not isinstance(bootstrap_all, dict):
        return ()
    return _coerce_technology_list(bootstrap_all.get("enable_components"), aliases)


def resolve_component_manifest_path(
    cfg: dict[str, Any],
    *,
    component: str,
    aliases: dict[str, str],
) -> str:
    hooks = _adapter_hooks(cfg)
    bootstrap_all = hooks.get("bootstrap_all")
    canonical_component = canonicalize_technology(component, aliases)
    if isinstance(bootstrap_all, dict):
        mapping = bootstrap_all.get("component_manifests")
        if isinstance(mapping, dict):
            candidate = str(mapping.get(canonical_component) or "").strip()
            if candidate:
                return candidate
    raise ConfigError(
        "adapter_hooks.bootstrap_all.component_manifests must define a manifest path "
        f"for component '{canonical_component}'."
    )


def resolve_component_deployment_name(
    cfg: dict[str, Any],
    *,
    component: str,
    aliases: dict[str, str],
) -> str:
    hooks = _adapter_hooks(cfg)
    bootstrap_all = hooks.get("bootstrap_all")
    canonical_component = canonicalize_technology(component, aliases)
    if isinstance(bootstrap_all, dict):
        mapping = bootstrap_all.get("component_deployments")
        if isinstance(mapping, dict):
            candidate = normalize_technology_token(mapping.get(canonical_component))
            if candidate:
                return candidate
    raise ConfigError(
        "adapter_hooks.bootstrap_all.component_deployments must define a deployment name "
        f"for component '{canonical_component}'."
    )


def resolve_pipeline_components(
    cfg: dict[str, Any],
    *,
    pipeline: str,
    aliases: dict[str, str],
    role_bindings: dict[str, str],
) -> dict[str, str]:
    hooks = _adapter_hooks(cfg)
    section = hooks.get(pipeline)
    if not isinstance(section, dict):
        raise ConfigError(
            f"adapter_hooks.{pipeline} must be defined as an object with a components map."
        )

    # Auto-derive from technology_bindings: each binding key is a component
    resolved: dict[str, str] = {}
    for binding_key, technology in role_bindings.items():
        if technology:
            resolved[binding_key] = technology

    # Overlay explicit components from config (for non-binding mappings like
    # indexer_manager: {technology: <service>})
    components = section.get("components") if isinstance(section, dict) else None
    if isinstance(components, dict):
        for key, value in components.items():
            component_key = str(key or "").strip()
            if not component_key:
                continue
            technology = ""
            if isinstance(value, dict):
                binding_key = str(value.get("binding") or "").strip()
                if binding_key:
                    technology = str(role_bindings.get(binding_key) or "").strip()
                if not technology:
                    technology = canonicalize_technology(value.get("technology"), aliases)
            else:
                technology = canonicalize_technology(value, aliases)
            technology = str(technology or "").strip()
            if technology:
                resolved[component_key] = technology

    if not resolved:
        raise ConfigError(
            f"adapter_hooks.{pipeline}: no components resolved from "
            "technology_bindings or explicit components map."
        )
    return resolved


def _lookup_path(context: dict[str, Any], path: str) -> tuple[bool, Any]:
    token = str(path or "").strip()
    if not token:
        return False, None
    cursor: Any = context
    for part in token.split("."):
        key = str(part or "").strip()
        if not key:
            return False, None
        if isinstance(cursor, dict) and key in cursor:
            cursor = cursor.get(key)
            continue
        return False, None
    return True, cursor


def evaluate_phase_condition(condition: Any, *, context: dict[str, Any]) -> bool:
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    if isinstance(condition, list):
        return all(evaluate_phase_condition(item, context=context) for item in condition)
    if not isinstance(condition, dict):
        return bool(condition)

    if "all_of" in condition:
        all_of = condition.get("all_of")
        if not isinstance(all_of, list):
            return False
        return all(evaluate_phase_condition(item, context=context) for item in all_of)
    if "any_of" in condition:
        any_of = condition.get("any_of")
        if not isinstance(any_of, list):
            return False
        return any(evaluate_phase_condition(item, context=context) for item in any_of)
    if "not" in condition:
        return not evaluate_phase_condition(condition.get("not"), context=context)

    exists = False
    value: Any = None
    if "var" in condition:
        exists, value = _lookup_path(context, str(condition.get("var") or ""))
    elif "value" in condition:
        exists = True
        value = condition.get("value")
    else:
        return False

    if "exists" in condition:
        expected_exists = bool(condition.get("exists"))
        if exists != expected_exists:
            return False
    if "equals" in condition:
        if value != condition.get("equals"):
            return False
    if "not_equals" in condition:
        if value == condition.get("not_equals"):
            return False
    if "in" in condition:
        choices = condition.get("in")
        if not isinstance(choices, list):
            return False
        if value not in choices:
            return False
    if "not_in" in condition:
        choices = condition.get("not_in")
        if not isinstance(choices, list):
            return False
        if value in choices:
            return False
    if "truthy" in condition:
        expected_truthy = bool(condition.get("truthy"))
        if bool(value) != expected_truthy:
            return False
    return True


def resolve_phase_skip_flag_specs(
    cfg: dict[str, Any],
    *,
    pipeline: str,
) -> tuple[PhaseSkipFlagSpec, ...]:
    plan = resolve_pipeline_phase_plan(cfg, pipeline=pipeline, allow_empty=True)
    if not plan:
        return ()

    configured_aliases = _resolve_skip_flag_aliases(cfg)
    out: list[PhaseSkipFlagSpec] = []
    seen: set[str] = set()
    for step in plan:
        key = normalize_flag_token(step.skip_flag)
        if not key or key in seen:
            continue
        seen.add(key)
        generic_option = f"--{key.replace('_', '-')}"
        option_strings = [generic_option]
        env_vars = [key.upper()]
        legacy = configured_aliases.get(key) or {}
        for opt in legacy.get("options", ()):
            token = str(opt).strip()
            if token and token not in option_strings:
                option_strings.append(token)
        for env in legacy.get("env_vars", ()):
            token = str(env).strip()
            if token and token not in env_vars:
                env_vars.append(token)
        operation_label = str(step.operation or "").strip()
        if operation_label == "run":
            action = str((step.params or {}).get("action") or "").strip()
            if action:
                operation_label = f"run:{action}"
        out.append(
            PhaseSkipFlagSpec(
                key=key,
                option_strings=tuple(option_strings),
                env_vars=tuple(env_vars),
                help=(
                    f"Skip '{operation_label}' phase(s) from adapter_hooks.{pipeline}.phase_plan "
                    f"(flag key: {key})."
                ),
            )
        )
    return tuple(out)


def _resolve_scale_policy_lists(
    cfg: dict[str, Any],
    *,
    aliases: dict[str, str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    # 1. If config.json has explicit scale_policy, use it (per-deploy override)
    hooks = _adapter_hooks(cfg)
    scale_policy = hooks.get("scale_policy")
    if isinstance(scale_policy, dict) and scale_policy.get("apps"):
        managed_apps = _coerce_technology_list(scale_policy.get("apps"), aliases)
        if managed_apps:
            scale_to_zero_apps = _coerce_technology_list(
                scale_policy.get("scale_to_zero_apps"), aliases
            )
            filtered = tuple(t for t in scale_to_zero_apps if t in managed_apps)
            return managed_apps, filtered

    # 2. Derive from per-service YAML registry flags
    try:
        from media_stack.core.service_registry.registry import (
            get_scalable_services,
            get_scale_to_zero_services,
        )
        scalable = get_scalable_services()
        if scalable:
            managed_apps = tuple(s.id for s in scalable)
            scale_to_zero = tuple(
                s.id for s in get_scale_to_zero_services() if s.id in managed_apps
            )
            return managed_apps, scale_to_zero
    except Exception as exc:
        log_swallowed(exc)

    raise ConfigError(
        "Could not resolve scale policy: no adapter_hooks.scale_policy.apps "
        "and service registry unavailable."
    )


def resolve_bootstrap_component_plan(config_file: Path) -> ControllerComponentPlan:
    cfg = load_bootstrap_config(config_file)
    catalog = build_manifest_catalog()
    role_bindings = resolve_role_bindings(cfg, aliases=catalog.aliases)
    technology_settings = resolve_technology_settings(
        cfg,
        aliases=catalog.aliases,
        technologies=catalog.technologies,
        role_bindings=role_bindings,
    )
    managed_apps, scale_to_zero_apps = _resolve_scale_policy_lists(
        cfg,
        aliases=catalog.aliases,
    )

    return ControllerComponentPlan(
        config=cfg,
        aliases=catalog.aliases,
        role_bindings=role_bindings,
        managed_apps=managed_apps or (),
        scale_to_zero_apps=scale_to_zero_apps or (),
        technology_settings=technology_settings,
    )
