"""Resolve active bootstrap components from config + plugin manifests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bootstrap_services.plugin_manifest_loader import load_plugin_manifests
from bootstrap_services.top_level_config_model import TopLevelBootstrapConfig
from core.exceptions import ConfigError


def normalize_technology_token(value: Any) -> str:
    token = str(value or "").strip().lower()
    token = re.sub(r"[^a-z0-9]+", "-", token)
    return token.strip("-")


def canonicalize_technology(value: Any, aliases: dict[str, str]) -> str:
    token = normalize_technology_token(value)
    if not token:
        return ""
    return aliases.get(token, token)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for item in values:
        token = str(item or "").strip()
        if token and token not in out:
            out.append(token)
    return tuple(out)


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


def _enabled_section(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(value.get("enabled"))


@dataclass(frozen=True)
class ManifestCatalog:
    aliases: dict[str, str] = field(default_factory=dict)
    runtime_technologies: tuple[str, ...] = field(default_factory=tuple)
    auxiliary_technologies: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BootstrapComponentPlan:
    config: dict[str, Any]
    aliases: dict[str, str]
    role_bindings: dict[str, str]
    core_apps: tuple[str, ...]
    worker_apps: tuple[str, ...]
    download_clients: dict[str, dict[str, Any]]


def load_bootstrap_config(config_file: Path) -> dict[str, Any]:
    path = Path(config_file)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError("Bootstrap config root must be an object.")
    try:
        return TopLevelBootstrapConfig.from_dict(payload).to_dict()
    except ValueError as exc:
        raise ConfigError(f"Invalid bootstrap config: {exc}") from exc


def build_manifest_catalog() -> ManifestCatalog:
    aliases: dict[str, str] = {}
    runtime_technologies: list[str] = []
    auxiliary_technologies: list[str] = []

    for manifest in load_plugin_manifests():
        technology = normalize_technology_token(manifest.technology)
        if not technology:
            continue
        aliases[technology] = technology
        for alias in manifest.aliases:
            token = normalize_technology_token(alias)
            if token and token not in aliases:
                aliases[token] = technology

        has_runtime_contract = bool(manifest.adapter_classes) or bool(manifest.app_service_classes)
        target = runtime_technologies if has_runtime_contract else auxiliary_technologies
        if technology not in target:
            target.append(technology)

    return ManifestCatalog(
        aliases=aliases,
        runtime_technologies=tuple(runtime_technologies),
        auxiliary_technologies=tuple(auxiliary_technologies),
    )


def resolve_role_bindings(
    cfg: dict[str, Any],
    *,
    aliases: dict[str, str],
) -> dict[str, str]:
    bindings = cfg.get("technology_bindings")
    if not isinstance(bindings, dict):
        bindings = {}

    request_manager = canonicalize_technology(bindings.get("request_manager"), aliases)
    if not request_manager:
        request_manager = canonicalize_technology("jellyseerr", aliases) or "jellyseerr"

    return {
        "torrent_client": canonicalize_technology(bindings.get("torrent_client"), aliases),
        "usenet_client": canonicalize_technology(bindings.get("usenet_client"), aliases),
        "media_server": canonicalize_technology(bindings.get("media_server"), aliases),
        "request_manager": request_manager,
    }


def resolve_download_clients(
    cfg: dict[str, Any],
    *,
    aliases: dict[str, str],
) -> dict[str, dict[str, Any]]:
    raw_clients = cfg.get("download_clients")
    if not isinstance(raw_clients, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw_clients.items():
        token = canonicalize_technology(key, aliases)
        if token and isinstance(value, dict):
            out[token] = dict(value)
    return out


def resolve_runner_phase_script(
    cfg: dict[str, Any],
    *,
    phase_key: str,
    technology: str,
    aliases: dict[str, str] | None = None,
    default: str = "",
) -> str:
    normalized_aliases = dict(aliases or {})
    hooks = _adapter_hooks(cfg)
    mappings = hooks.get("runner_phase_scripts")
    if isinstance(mappings, dict):
        phase_map = mappings.get(phase_key)
        if isinstance(phase_map, dict):
            candidates: list[str] = []
            raw_tech = normalize_technology_token(technology)
            canonical = canonicalize_technology(raw_tech, normalized_aliases)
            for token in (raw_tech, canonical, "*"):
                if token and token not in candidates:
                    candidates.append(token)
            for token in candidates:
                candidate = str(phase_map.get(token) or "").strip()
                if candidate:
                    return candidate
    return str(default or "").strip()


def resolve_bootstrap_enable_workers(
    cfg: dict[str, Any],
    *,
    aliases: dict[str, str],
    fallback_workers: tuple[str, ...] = (),
) -> tuple[str, ...]:
    hooks = _adapter_hooks(cfg)
    bootstrap_all = hooks.get("bootstrap_all")
    if isinstance(bootstrap_all, dict) and "enable_workers" in bootstrap_all:
        workers = bootstrap_all.get("enable_workers")
        return _coerce_technology_list(workers, aliases)

    out: list[str] = []
    for worker in fallback_workers:
        token = canonicalize_technology(worker, aliases)
        if token and token not in out:
            out.append(token)
    return tuple(out)


def resolve_worker_manifest_path(
    cfg: dict[str, Any],
    *,
    worker: str,
    aliases: dict[str, str],
    default: str | None = None,
) -> str:
    hooks = _adapter_hooks(cfg)
    bootstrap_all = hooks.get("bootstrap_all")
    canonical_worker = canonicalize_technology(worker, aliases)
    if isinstance(bootstrap_all, dict):
        mapping = bootstrap_all.get("worker_manifests")
        if isinstance(mapping, dict):
            candidate = str(mapping.get(canonical_worker) or "").strip()
            if candidate:
                return candidate
    if default is not None:
        return str(default)
    return f"k8s/{canonical_worker}.yaml"


def resolve_worker_deployment_name(
    cfg: dict[str, Any],
    *,
    worker: str,
    aliases: dict[str, str],
    default: str | None = None,
) -> str:
    hooks = _adapter_hooks(cfg)
    bootstrap_all = hooks.get("bootstrap_all")
    canonical_worker = canonicalize_technology(worker, aliases)
    if isinstance(bootstrap_all, dict):
        mapping = bootstrap_all.get("worker_deployments")
        if isinstance(mapping, dict):
            candidate = normalize_technology_token(mapping.get(canonical_worker))
            if candidate:
                return candidate
    if default is not None:
        explicit = normalize_technology_token(default)
        if explicit:
            return explicit
    return canonical_worker


def _resolve_explicit_scale_list(
    cfg: dict[str, Any],
    *,
    list_key: str,
    aliases: dict[str, str],
) -> tuple[str, ...] | None:
    hooks = _adapter_hooks(cfg)
    scale_policy = hooks.get("scale_policy")
    if not isinstance(scale_policy, dict):
        return None
    if list_key not in scale_policy:
        return None
    return _coerce_technology_list(scale_policy.get(list_key), aliases)


def _derive_default_core_apps(
    cfg: dict[str, Any],
    *,
    aliases: dict[str, str],
    catalog: ManifestCatalog,
    role_bindings: dict[str, str],
) -> tuple[str, ...]:
    apps: list[str] = []

    for role_key in ("media_server", "request_manager", "torrent_client", "usenet_client"):
        token = str(role_bindings.get(role_key) or "").strip()
        if token and token not in apps:
            apps.append(token)

    arr_apps = cfg.get("arr_apps")
    if isinstance(arr_apps, list):
        for item in arr_apps:
            if not isinstance(item, dict):
                continue
            token = canonicalize_technology(
                item.get("implementation") or item.get("name"),
                aliases,
            )
            if token and token not in apps:
                apps.append(token)

    if str(cfg.get("prowlarr_url") or "").strip():
        prowlarr = canonicalize_technology("prowlarr", aliases)
        if prowlarr and prowlarr not in apps:
            apps.append(prowlarr)

    for technology in catalog.runtime_technologies:
        if _enabled_section(cfg.get(technology)) and technology not in apps:
            apps.append(technology)

    return _dedupe(apps)


def _derive_default_worker_apps(
    cfg: dict[str, Any],
    *,
    aliases: dict[str, str],
    catalog: ManifestCatalog,
) -> tuple[str, ...]:
    workers: list[str] = []
    for technology in catalog.auxiliary_technologies:
        if _enabled_section(cfg.get(technology)) and technology not in workers:
            workers.append(technology)

    configured_workers = resolve_bootstrap_enable_workers(cfg, aliases=aliases)
    for technology in configured_workers:
        if technology and technology not in workers:
            workers.append(technology)
    return _dedupe(workers)


def resolve_bootstrap_component_plan(config_file: Path) -> BootstrapComponentPlan:
    cfg = load_bootstrap_config(config_file)
    catalog = build_manifest_catalog()
    role_bindings = resolve_role_bindings(cfg, aliases=catalog.aliases)
    download_clients = resolve_download_clients(cfg, aliases=catalog.aliases)

    explicit_core = _resolve_explicit_scale_list(
        cfg,
        list_key="core_apps",
        aliases=catalog.aliases,
    )
    core_apps = explicit_core
    if explicit_core is None:
        core_apps = _derive_default_core_apps(
            cfg,
            aliases=catalog.aliases,
            catalog=catalog,
            role_bindings=role_bindings,
        )

    explicit_workers = _resolve_explicit_scale_list(
        cfg,
        list_key="worker_apps",
        aliases=catalog.aliases,
    )
    worker_apps = explicit_workers
    if explicit_workers is None:
        worker_apps = _derive_default_worker_apps(
            cfg,
            aliases=catalog.aliases,
            catalog=catalog,
        )

    return BootstrapComponentPlan(
        config=cfg,
        aliases=catalog.aliases,
        role_bindings=role_bindings,
        core_apps=core_apps or (),
        worker_apps=worker_apps or (),
        download_clients=download_clients,
    )
