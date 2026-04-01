"""Resolve bootstrap job config from cluster/runtime context."""

from __future__ import annotations

import importlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bootstrap_services.plugin_manifest_loader import (
    collect_config_resolver_handlers,
    load_plugin_manifests,
)
from bootstrap_services.top_level_config_model import TopLevelBootstrapConfig
from core.exceptions import ConfigError
from core.kube import KubectlClient

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class BootstrapConfigResolverConfig:
    namespace: str
    ingress_name: str
    config_file: Path
    job_config_file: Path


@dataclass
class BootstrapConfigResolverService:
    cfg: BootstrapConfigResolverConfig
    kube: KubectlClient
    info: LogFn

    def _resolve_resolver_cfg(self, cfg: dict[str, Any]) -> dict[str, Any] | None:
        adapter_hooks = cfg.get("adapter_hooks")
        if not isinstance(adapter_hooks, dict):
            return None
        bootstrap_job = adapter_hooks.get("bootstrap_job")
        if not isinstance(bootstrap_job, dict):
            return None
        resolver_cfg = bootstrap_job.get("config_resolver")
        if resolver_cfg is None:
            return None
        if not isinstance(resolver_cfg, dict):
            raise ConfigError("adapter_hooks.bootstrap_job.config_resolver must be an object.")
        return resolver_cfg

    @staticmethod
    def _load_handler_from_spec(operation_name: str, spec: str) -> Callable[..., Any]:
        raw = str(spec or "").strip()
        if ":" not in raw:
            raise ConfigError(
                "bootstrap_job config resolver handler spec for "
                f"'{operation_name}' is invalid: expected 'module.path:callable_name'."
            )
        module_name, attr_name = raw.rsplit(":", 1)
        module_name = module_name.strip()
        attr_name = attr_name.strip()
        if not module_name or not attr_name:
            raise ConfigError(
                "bootstrap_job config resolver handler spec for "
                f"'{operation_name}' is invalid: expected 'module.path:callable_name'."
            )
        module = importlib.import_module(module_name)
        handler = getattr(module, attr_name, None)
        if handler is None or not callable(handler) or not inspect.isroutine(handler):
            raise ConfigError(
                "bootstrap_job config resolver handler spec for "
                f"'{operation_name}' does not resolve to a callable: {raw}"
            )
        return handler

    @staticmethod
    def _resolve_operations(resolver_cfg: dict[str, Any]) -> tuple[str, ...]:
        raw_operations = resolver_cfg.get("operations")
        if not isinstance(raw_operations, list):
            raise ConfigError(
                "adapter_hooks.bootstrap_job.config_resolver.operations must be a non-empty array."
            )
        operations: list[str] = []
        for index, raw in enumerate(raw_operations):
            op = str(raw or "").strip()
            if not op:
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.config_resolver.operations"
                    f"[{index}] must be a non-empty string."
                )
            operations.append(op)
        if not operations:
            raise ConfigError(
                "adapter_hooks.bootstrap_job.config_resolver.operations must be non-empty."
            )
        return tuple(operations)

    @staticmethod
    def _resolve_handler_overrides(resolver_cfg: dict[str, Any]) -> dict[str, str]:
        raw_overrides = resolver_cfg.get("handler_specs")
        if raw_overrides is None:
            return {}
        if not isinstance(raw_overrides, dict):
            raise ConfigError(
                "adapter_hooks.bootstrap_job.config_resolver.handler_specs must be an object/map."
            )
        overrides: dict[str, str] = {}
        for raw_name, raw_spec in raw_overrides.items():
            operation_name = str(raw_name or "").strip()
            if not operation_name:
                continue
            spec = str(raw_spec or "").strip()
            if not spec:
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.config_resolver.handler_specs."
                    f"{operation_name} must be a non-empty handler spec string."
                )
            overrides[operation_name] = spec
        return overrides

    def _load_json(self, path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ConfigError(f"Expected JSON object in {path}")
        try:
            return TopLevelBootstrapConfig.from_dict(data).to_dict()
        except ValueError as exc:
            raise ConfigError(f"Invalid bootstrap config at {path}: {exc}") from exc

    def resolve_bootstrap_config(self) -> None:
        cfg = self._load_json(self.cfg.config_file)
        resolver_cfg = self._resolve_resolver_cfg(cfg)
        if resolver_cfg is None:
            self.info(
                "No bootstrap config resolver declared at "
                "adapter_hooks.bootstrap_job.config_resolver; "
                "using bootstrap config as-is."
            )
            self.cfg.job_config_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
            self.info(f"Resolved job config: {self.cfg.job_config_file}")
            return

        operations = self._resolve_operations(resolver_cfg)
        handler_specs = collect_config_resolver_handlers(load_plugin_manifests())
        handler_specs.update(self._resolve_handler_overrides(resolver_cfg))

        for operation_name in operations:
            spec = str(handler_specs.get(operation_name) or "").strip()
            if not spec:
                available = ", ".join(sorted(handler_specs.keys())) or "<none>"
                raise ConfigError(
                    "No bootstrap config resolver handler is registered for "
                    f"operation '{operation_name}'. "
                    "Register it in plugin manifest config_resolver_handlers or in "
                    "adapter_hooks.bootstrap_job.config_resolver.handler_specs. "
                    f"Available operations: {available}."
                )
            handler = self._load_handler_from_spec(operation_name, spec)
            result = handler(
                cfg,
                resolver_cfg=resolver_cfg,
                kube=self.kube,
                namespace=self.cfg.namespace,
                ingress_name=self.cfg.ingress_name,
                info=self.info,
            )
            if result is None:
                continue
            if not isinstance(result, dict):
                raise ConfigError(
                    "bootstrap config resolver handler "
                    f"'{operation_name}' returned unsupported type "
                    f"'{type(result).__name__}' (expected dict or None)."
                )
            cfg = result

        self.cfg.job_config_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        self.info(f"Resolved job config: {self.cfg.job_config_file}")
