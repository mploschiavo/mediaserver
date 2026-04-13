"""Config and hook resolution methods for DeployStackRunner.

Extracted from deploy_stack_main.py — all methods that resolve configuration
from the bootstrap config JSON, adapter hooks, edge hooks, profile catalog,
and provider registries.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from media_stack.core.auth.provider_registry import (
    compose_service_names_by_provider,
    load_builtin_auth_provider_specs,
    merge_auth_provider_defaults,
)
from media_stack.core.controller_profile import load_bootstrap_profile_catalog
from media_stack.core.edge.provider_registry import (
    compose_label_specs_by_provider,
    router_service_names_by_provider,
)

from media_stack.cli.commands.deploy_stack_errors import DeployError
from media_stack.cli.workflows import deploy_hook_config_resolver

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import DeployStackConfig


class ConfigResolutionMixin:
    """Methods that resolve configuration from bootstrap config and hooks.

    Requires ``self.cfg: DeployStackConfig`` on the concrete class.
    """

    cfg: DeployStackConfig
    _resolved_config_cache: dict[str, object] | None

    # -- bootstrap config loading ------------------------------------------

    def _resolved_bootstrap_config(self) -> dict[str, object]:
        if self._resolved_config_cache is None:
            payload = json.loads(self.cfg.config_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise DeployError(
                    f"Expected JSON object in bootstrap config file: {self.cfg.config_file}"
                )
            self._resolved_config_cache = payload
        return self._resolved_config_cache

    def _rebuild_profile_actions(
        self,
    ) -> tuple[
        dict[str, tuple[str, ...]],
        dict[str, tuple[str, ...]],
        dict[str, str],
        dict[str, tuple[str, ...]],
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        try:
            return deploy_hook_config_resolver.profile_actions(self._resolved_bootstrap_config())
        except ValueError as exc:
            raise DeployError(str(exc)) from exc

    def _bootstrap_job_hooks(self) -> dict[str, object]:
        cfg = self._resolved_bootstrap_config()
        adapter_hooks = cfg.get("adapter_hooks")
        if not isinstance(adapter_hooks, dict):
            return {}
        bootstrap_job = adapter_hooks.get("bootstrap_job")
        if not isinstance(bootstrap_job, dict):
            return {}
        return bootstrap_job

    def _edge_hooks(self) -> dict[str, object]:
        cfg = self._resolved_bootstrap_config()
        adapter_hooks = cfg.get("adapter_hooks")
        if not isinstance(adapter_hooks, dict):
            return {}
        edge = adapter_hooks.get("edge")
        if not isinstance(edge, dict):
            return {}
        return edge

    # -- edge routing resolution -------------------------------------------

    def _ingress_class_priority(self) -> tuple[str, ...]:
        hooks = self._edge_hooks()
        raw = hooks.get("ingress_class_priority")
        if not isinstance(raw, list):
            return ()
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def _edge_router_provider(self) -> str:
        explicit = str(self.cfg.edge_router_provider or "").strip().lower()
        if explicit:
            return explicit
        hooks = self._edge_hooks()
        return str(hooks.get("router_provider") or "").strip().lower()

    def _edge_provider_hook_values(
        self,
        *,
        by_provider_key: str,
        fallback_key: str,
    ) -> tuple[str, ...]:
        hooks = self._edge_hooks()
        provider = self._edge_router_provider()
        values: list[str] = []
        seen: set[str] = set()

        raw_by_provider = hooks.get(by_provider_key)
        selected_from_provider_map = False
        if isinstance(raw_by_provider, dict) and provider:
            provider_values = raw_by_provider.get(provider)
            if isinstance(provider_values, list):
                selected_from_provider_map = True
                for item in provider_values:
                    token = str(item or "").strip().lower()
                    if not token or token in seen:
                        continue
                    seen.add(token)
                    values.append(token)

        if not selected_from_provider_map:
            raw_fallback = hooks.get(fallback_key)
            if isinstance(raw_fallback, list):
                for item in raw_fallback:
                    token = str(item or "").strip().lower()
                    if not token or token in seen:
                        continue
                    seen.add(token)
                    values.append(token)

        return tuple(values)

    def _edge_router_service_names(self) -> tuple[str, ...]:
        provider_defaults = router_service_names_by_provider()
        provider = self._edge_router_provider()
        out: list[str] = []
        seen: set[str] = set()
        for item in tuple(provider_defaults.get(provider) or ()):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        for item in self._edge_provider_hook_values(
            by_provider_key="router_service_names_by_provider",
            fallback_key="router_service_names",
        ):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def _edge_path_prefix_redirect_service_names(self) -> tuple[str, ...]:
        # 1. Config.json override (per-deployment customization)
        out: list[str] = []
        seen: set[str] = set()
        for item in self._edge_provider_hook_values(
            by_provider_key="path_prefix_redirect_service_names_by_provider",
            fallback_key="path_prefix_redirect_service_names",
        ):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        if out:
            return tuple(out)
        # 2. Derive from per-service YAML registry (web_ui=true)
        try:
            from media_stack.api.services.registry import get_web_ui_services
            svcs = get_web_ui_services()
            if svcs:
                return tuple(s.id for s in svcs)
        except Exception as exc:
            import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        return ()

    def _edge_path_prefix_preserve_service_names(self) -> tuple[str, ...]:
        # 1. Config.json override (per-deployment customization)
        out: list[str] = []
        seen: set[str] = set()
        for item in self._edge_provider_hook_values(
            by_provider_key="path_prefix_preserve_service_names_by_provider",
            fallback_key="path_prefix_preserve_service_names",
        ):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        if out:
            return tuple(out)
        # 2. Derive from per-service YAML registry (preserve_path_prefix=true)
        try:
            from media_stack.api.services.registry import get_preserve_path_prefix_services
            svcs = get_preserve_path_prefix_services()
            if svcs:
                return tuple(s.id for s in svcs)
        except Exception as exc:
            import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        return ()

    def _edge_compose_provider_specs(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {
            provider: dict(spec) for provider, spec in compose_label_specs_by_provider().items()
        }
        hooks = self._edge_hooks()
        raw = hooks.get("compose_provider_specs")
        if isinstance(raw, dict):
            for raw_provider, raw_spec in raw.items():
                provider = str(raw_provider or "").strip().lower()
                if not provider or not isinstance(raw_spec, dict):
                    continue
                merged_spec = dict(out.get(provider) or {})
                for raw_key, raw_value in raw_spec.items():
                    key = str(raw_key or "").strip()
                    value = str(raw_value or "").strip()
                    if key and value:
                        merged_spec[key] = value
                out[provider] = merged_spec
        return out

    def _media_server_service_names(self) -> tuple[str, ...]:
        # 1. adapter_hooks.edge (config.json / K8s YAML)
        hooks = self._edge_hooks()
        raw = hooks.get("media_server_service_names")
        out: list[str] = []
        seen: set[str] = set()
        if isinstance(raw, list):
            for item in raw:
                token = str(item or "").strip().lower()
                if not token or token in seen:
                    continue
                seen.add(token)
                out.append(token)
        if out:
            return tuple(out)
        # 2. Profile YAML routing.media_server_service_names
        try:
            from media_stack.core.controller_profile import load_bootstrap_profile
            profile = load_bootstrap_profile()
            routing = profile.get("routing") or {}
            raw_profile = routing.get("media_server_service_names")
            if isinstance(raw_profile, list) and raw_profile:
                return tuple(str(s).strip().lower() for s in raw_profile if str(s).strip())
        except Exception as exc:
            import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        # 3. Derive from technology_bindings
        cfg = self._resolved_bootstrap_config()
        technology_bindings = cfg.get("technology_bindings")
        if isinstance(technology_bindings, dict):
            token = str(technology_bindings.get("media_server") or "").strip().lower()
            if token:
                return (token,)
        return ()

    # -- auth / route / provider validation --------------------------------

    def _auth_provider_middleware_defaults(self) -> dict[str, str]:
        catalog = load_bootstrap_profile_catalog()
        hooks = self._edge_hooks()
        hook_defaults: dict[str, str] = {}
        raw = hooks.get("auth_provider_middleware_defaults")
        if isinstance(raw, dict):
            for raw_key, raw_value in raw.items():
                key = str(raw_key or "").strip().lower()
                if not key:
                    continue
                hook_defaults[key] = str(raw_value or "").strip()

        provider_keys: list[str] = []
        seen: set[str] = set()
        for raw_key in (
            *tuple(catalog.auth_providers),
            str(catalog.auth_disabled_provider or "").strip().lower(),
            *(spec.key for spec in load_builtin_auth_provider_specs()),
            *tuple(hook_defaults.keys()),
        ):
            key = str(raw_key or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            provider_keys.append(key)
        return merge_auth_provider_defaults(
            provider_keys=tuple(provider_keys),
            catalog_defaults=dict(catalog.auth_provider_middleware_defaults or {}),
            override_defaults=hook_defaults,
        )

    def _valid_route_strategies(self) -> tuple[str, ...]:
        catalog = load_bootstrap_profile_catalog()
        values = tuple(dict.fromkeys(catalog.route_strategy_aliases.values()))
        return tuple(str(value).strip().lower() for value in values if str(value).strip())

    def _valid_auth_providers(self) -> tuple[str, ...]:
        catalog = load_bootstrap_profile_catalog()
        values: list[str] = []
        seen: set[str] = set()
        for token in (
            *tuple(catalog.auth_providers),
            str(catalog.auth_disabled_provider or "").strip().lower(),
            *tuple(self._auth_provider_middleware_defaults().keys()),
        ):
            normalized = str(token or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            values.append(normalized)
        return tuple(values)

    def _valid_edge_router_providers(self) -> tuple[str, ...]:
        providers = {
            str(provider or "").strip().lower()
            for provider in self._edge_compose_provider_specs().keys()
            if str(provider or "").strip()
        }
        return tuple(sorted(providers))

    # -- bootstrap job / policy config -------------------------------------

    def _runtime_config_policy_handler_spec(self) -> str:
        hooks = self._bootstrap_job_hooks()
        spec = str(hooks.get("runtime_config_policy_handler") or "").strip()
        if spec and ":" not in spec:
            raise DeployError(
                "adapter_hooks.bootstrap_job.runtime_config_policy_handler "
                "must be module.path:Symbol"
            )
        return spec

    def _runtime_config_policy_params(self) -> dict[str, object]:
        return {
            "selected_apps_csv": self.cfg.selected_apps,
            "preconfigure_api_keys": self._is_truthy(self.cfg.preconfigure_api_keys),
            "auto_download_content": self._is_truthy(self.cfg.auto_download_content),
            "internet_exposed": self._is_truthy(self.cfg.internet_exposed),
            "route_strategy": self.cfg.route_strategy,
            "auth_provider": self.cfg.auth_provider,
            "auth_middleware": self.cfg.auth_middleware,
            "edge_router_provider": self._edge_router_provider(),
            "ingress_domain": self.cfg.ingress_domain,
            "app_gateway_host": self.cfg.app_gateway_host,
            "app_gateway_port": self.cfg.app_gateway_port,
            "app_path_prefix": self.cfg.app_path_prefix,
            "media_server_direct_host": self.cfg.media_server_direct_host,
        }

    def _compose_passthrough_env_vars(self) -> tuple[str, ...]:
        env_vars: list[str] = ["STACK_ADMIN_USERNAME", "STACK_ADMIN_PASSWORD"]
        hooks = self._bootstrap_job_hooks()
        secret_targets = hooks.get("secret_priming_targets")
        if isinstance(secret_targets, dict):
            for spec in secret_targets.values():
                if not isinstance(spec, dict):
                    continue
                name = str(spec.get("env_var") or "").strip()
                if name:
                    env_vars.append(name)
        deduped: list[str] = []
        seen: set[str] = set()
        for raw_name in env_vars:
            name = str(raw_name or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        return tuple(deduped)

    def _compose_preflight_handlers(self) -> tuple[str, ...]:
        try:
            return deploy_hook_config_resolver.compose_preflight_handlers(
                self._bootstrap_job_hooks()
            )
        except ValueError as exc:
            raise DeployError(str(exc)) from exc

