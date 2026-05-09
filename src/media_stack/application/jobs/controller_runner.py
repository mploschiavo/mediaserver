"""Runner build and config policy for the bootstrap controller."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

import media_stack.services.runtime_platform as runtime_platform
import media_stack.services.runtime_secrets as runtime_secrets
from media_stack.application.jobs.controller_handlers import _resolve_config_path
from media_stack.core.service_registry.registry import SERVICES as _REGISTRY_SERVICES
from media_stack.services.apps.stack.controller_config_policy import (
    apply_bootstrap_runtime_policy,
)
from media_stack.services.controller_service import (
    ControllerDependencies,
    ControllerService,
)
from media_stack.services.enums import BootstrapMode
from media_stack.services.operation_wiring import build_runner_event_registry
from media_stack.services.runtime_factory import (
    ControllerCliArgs,
    ControllerRuntimeFactoryDependencies,
    ControllerRuntimeFactoryService,
)


_DEFAULT_ROUTE_STRATEGY = "hybrid"
_DEFAULT_BASE_DOMAIN = "local"
_DEFAULT_PATH_PREFIX = "/app"
_DEFAULT_PRECONFIGURE = "1"
_DEFAULT_AUTO_DOWNLOAD = "1"
_PATH_PREFIX_STRATEGIES = ("hybrid", "path-prefix")


class ControllerRunnerBuilder:
    """Build the bootstrap runner + resolve the config policy.

    Constructor-injected getters keep the class testable without
    monkey-patching ``os.environ``.
    """

    def __init__(
        self,
        *,
        env: Callable[[str, str], str] | None = None,
        path_factory: Callable[[str], Path] | None = None,
        yaml_loader: Callable[[Any], Any] | None = None,
        registry_services: Any = None,
        platform: Any = None,
        secrets: Any = None,
        policy_applier: Callable[..., None] | None = None,
    ) -> None:
        self._env = env or os.environ.get
        self._path_factory = path_factory or Path
        self._yaml_loader = yaml_loader or yaml.safe_load
        self._registry_services = (
            registry_services if registry_services is not None
            else _REGISTRY_SERVICES
        )
        self._platform = platform or runtime_platform
        self._secrets = secrets or runtime_secrets
        self._policy_applier = policy_applier or apply_bootstrap_runtime_policy

    # -- Config policy ---------------------------------------------------

    def build_config_policy(self) -> object | None:
        """Build a config policy callable from the profile YAML."""
        profile_file = self._env("BOOTSTRAP_PROFILE_FILE", "")
        if not profile_file:
            return None

        path = self._path_factory(profile_file)
        if not path.is_file():
            return None

        profile = self._load_profile(path)
        if profile is None:
            return None

        return self._policy_from_profile(profile)

    def _load_profile(self, path: Path) -> dict | None:
        """Read + parse the profile YAML; return ``None`` on any error."""
        try:
            with open(path) as handle:
                return self._yaml_loader(handle) or {}
        except (OSError, yaml.YAMLError):
            return None

    def _policy_from_profile(self, profile: dict) -> Callable[[dict], None]:
        """Materialise the resolved-args dict and return a policy callable."""
        resolved = self._resolve_routing(profile)
        auto_download = self._resolve_auto_download(profile)

        def policy(cfg: dict) -> None:
            self._policy_applier(
                cfg,
                selected_apps_csv="",
                preconfigure_api_keys=self._env(
                    "PRECONFIGURE_API_KEYS", _DEFAULT_PRECONFIGURE,
                ) == "1",
                auto_download_content=auto_download,
                internet_exposed=resolved["internet_exposed"],
                route_strategy=resolved["route_strategy"],
                ingress_domain=resolved["base_domain"],
                app_gateway_host=resolved["gateway_host"],
                app_gateway_port=resolved["gateway_port"],
                app_path_prefix=resolved["path_prefix"],
                media_server_direct_host=resolved["media_server_direct_host"],
            )
            self._platform.log(
                f"[OK] Config policy applied (auto_download_content={auto_download})"
            )

        return policy

    def _resolve_routing(self, profile: dict) -> dict:
        """Pull every routing-shaped value the policy applier needs."""
        routing = profile.get("routing") or {}
        route_strategy = (
            routing.get("strategy")
            or self._env("ROUTE_STRATEGY", _DEFAULT_ROUTE_STRATEGY)
        )
        base_domain = routing.get("base_domain") or _DEFAULT_BASE_DOMAIN
        path_prefix = (
            routing.get("app_path_prefix")
            or self._env("APP_PATH_PREFIX", _DEFAULT_PATH_PREFIX)
        )
        gateway_port = (
            str(routing.get("gateway_port", ""))
            or self._env("APP_GATEWAY_PORT", "")
        )
        internet_exposed = bool(routing.get("internet_exposed"))
        stack_name = str((profile.get("metadata") or {}).get("name", "")).strip()
        stack_subdomain = routing.get("stack_subdomain") or stack_name

        gateway_host = self._derive_gateway_host(
            routing=routing,
            route_strategy=route_strategy,
            base_domain=base_domain,
            stack_subdomain=stack_subdomain,
        )
        media_server_direct_host = self._derive_media_server_host(
            routing=routing,
            base_domain=base_domain,
            stack_subdomain=stack_subdomain,
        )

        return {
            "route_strategy": route_strategy,
            "base_domain": base_domain,
            "path_prefix": path_prefix,
            "gateway_port": gateway_port,
            "internet_exposed": internet_exposed,
            "gateway_host": gateway_host,
            "media_server_direct_host": media_server_direct_host,
        }

    def _derive_gateway_host(
        self,
        *,
        routing: dict,
        route_strategy: str,
        base_domain: str,
        stack_subdomain: str,
    ) -> str:
        gateway_host = (
            routing.get("gateway_host")
            or self._env("APP_GATEWAY_HOST", "")
        )
        if (
            not gateway_host
            and route_strategy in _PATH_PREFIX_STRATEGIES
            and stack_subdomain
        ):
            parts = [p for p in ["apps", stack_subdomain, base_domain] if p]
            gateway_host = ".".join(parts).lower()
        return gateway_host

    def _derive_media_server_host(
        self,
        *,
        routing: dict,
        base_domain: str,
        stack_subdomain: str,
    ) -> str:
        direct_hosts = routing.get("direct_hosts") or {}
        media_server_direct_host = str(direct_hosts.get("media_server", ""))
        if (
            not media_server_direct_host
            and stack_subdomain
            and base_domain
        ):
            default_ms_id = self._default_media_server_id()
            media_server_id = str(
                direct_hosts.get("media_server_id", default_ms_id)
            )
            parts = [p for p in [media_server_id, stack_subdomain, base_domain] if p]
            media_server_direct_host = ".".join(parts).lower()
        return media_server_direct_host

    def _default_media_server_id(self) -> str:
        """First registry service tagged ``media`` with a host; falls
        back to the literal ``"media"`` if none exists."""
        for service in self._registry_services:
            if service.category == "media" and service.host:
                return service.id
        return "media"

    def _resolve_auto_download(self, profile: dict) -> bool:
        """Env var wins; otherwise the profile's ``bootstrap.auto_download_content``.

        Default is ``True``: a fresh deploy with neither env nor profile
        set auto-adds content (TMDb popular, etc.) so the *arr stack is
        immediately useful. Operators who want manual-only set
        ``AUTO_DOWNLOAD_CONTENT=0`` or
        ``profile.bootstrap.auto_download_content: false`` to opt out —
        both paths are still honored. The promise
        ``radarr-import-lists-auto`` (and its Sonarr sibling) asserts
        ``enableAuto=True`` on every enabled list; this default keeps
        them satisfied for unconfigured stacks.
        """
        # NOTE: ratchet test_runtime_invariants_ratchets requires the
        # literal substring ``profile_bootstrap.get("auto_download_content"``
        # below — keep it in this exact form.
        profile_bootstrap = profile.get("bootstrap") or {}
        profile_auto_download = bool(
            profile_bootstrap.get("auto_download_content", True)
        )
        env_raw = self._env("AUTO_DOWNLOAD_CONTENT", "").strip()
        if env_raw:
            return env_raw == "1"
        return profile_auto_download

    # -- Runner build ----------------------------------------------------

    def build_runner(
        self,
        args: argparse.Namespace,
        *,
        auto_prowlarr_indexers: bool = False,
    ) -> tuple:
        """Build the bootstrap runner and runtime state from CLI args."""
        build_sab_remote_path_mappings = self._resolve_sab_path_mappings()
        runtime_factory = self._make_runtime_factory(
            build_sab_remote_path_mappings,
        )
        resolved_config = _resolve_config_path(args.config) or args.config
        build_result = runtime_factory.build_from_cli(
            ControllerCliArgs(
                mode=BootstrapMode.from_cli(args.mode),
                config_path=resolved_config,
                config_root=args.config_root,
                wait_timeout=args.wait_timeout,
                auto_prowlarr_indexers=(
                    auto_prowlarr_indexers or args.auto_prowlarr_indexers
                ),
                runtime_env=str(args.env or "prod"),
            )
        )
        runtime_state = build_result.runtime
        self._platform.log(
            f"[INFO] Bootstrap plan: {build_result.plan.to_log_line()}"
        )
        runner_operations = build_runner_event_registry(
            event_handler_specs=(
                runtime_state.adapter_hooks_cfg or {}
            ).get("event_handlers"),
        )

        runner = ControllerService(
            deps=ControllerDependencies(
                log=self._platform.log,
                bool_cfg=self._platform.bool_cfg,
                normalize_url=self._platform.normalize_url,
                wait_for_service=self._platform.wait_for_service,
                operations=runner_operations,
            )
        )
        return runner, runtime_state

    def _resolve_sab_path_mappings(self) -> Callable[[dict], list]:
        """Optional dependency: SABnzbd remote-path mappings.

        If the SABnzbd app code is not installed we return a no-op
        mapper that always yields ``[]``.
        """
        try:
            servarr_runtime_arr_ops = importlib.import_module(
                "media_stack.services.apps.servarr.runtime.arr_ops"
            )
        except ImportError:
            self._platform.log(
                "[INFO] Usenet client path mappings not available -- skipping"
            )
            return self._noop_sab_mappings
        return getattr(
            servarr_runtime_arr_ops,
            "build_sab_remote_path_mappings",
            self._noop_sab_mappings,
        )

    def _noop_sab_mappings(self, cfg: dict) -> list:
        """Empty SABnzbd remote-path mapping fallback."""
        return []

    def _make_runtime_factory(
        self,
        build_sab_remote_path_mappings: Callable[[dict], list],
    ) -> ControllerRuntimeFactoryService:
        """Wire the runtime-factory service with platform helpers."""
        return ControllerRuntimeFactoryService(
            deps=ControllerRuntimeFactoryDependencies(
                load_bootstrap_default_json=(
                    self._platform.load_bootstrap_default_json
                ),
                deep_merge_objects=self._platform.deep_merge_objects,
                bool_cfg=self._platform.bool_cfg,
                coerce_list=self._platform.coerce_list,
                env_truthy=self._platform.env_truthy,
                read_api_key=self._secrets.read_api_key,
                build_sab_remote_path_mappings=build_sab_remote_path_mappings,
            ),
            config_policy=sys.modules[__name__]._build_config_policy(),
        )


_INSTANCE = ControllerRunnerBuilder()

# Module-level aliases preserve the legacy public+underscore import
# API. Tests ``mock.patch`` these dotted names directly; the dispatch
# in ``ControllerRunnerBuilder._make_runtime_factory`` routes through
# ``sys.modules[__name__]._build_config_policy`` so a patch lands.
_build_config_policy = _INSTANCE.build_config_policy
_build_runner = _INSTANCE.build_runner
build_config_policy = _INSTANCE.build_config_policy
build_runner = _INSTANCE.build_runner

__all__ = [
    "ControllerRunnerBuilder",
    "_build_config_policy",
    "_build_runner",
    "build_config_policy",
    "build_runner",
]
