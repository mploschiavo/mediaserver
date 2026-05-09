"""Writers for bootstrap-generated config artifacts.

Thin coordinator that delegates service-specific logic to app-scoped modules
under services.apps/.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ResolvePathFn = Callable[[str | Path, str], Path]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ResolveJellyfinApiKeyFn = Callable[[dict[str, Any], str], str]
JellyfinRequestFn = Callable[[str, str, str, str, Any, int], tuple[int, Any, str]]
LogFn = Callable[[str], None]
LoadBootstrapDefaultJsonFn = Callable[[str, dict[str, Any]], dict[str, Any]]
RenderHomepageServicesYamlFn = Callable[[list[str], str, dict[str, Any]], str]


@dataclass
class ConfigArtifactsService:
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    resolve_path: ResolvePathFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    resolve_jellyfin_api_key: ResolveJellyfinApiKeyFn
    jellyfin_request: JellyfinRequestFn
    log: LogFn
    load_bootstrap_default_json: LoadBootstrapDefaultJsonFn
    default_homepage_hosts: list[str]
    render_homepage_services_yaml: RenderHomepageServicesYamlFn

    # ------------------------------------------------------------------
    # YAML helpers (kept here — generic, not service-specific)
    # ------------------------------------------------------------------

    @staticmethod
    def yaml_scalar(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "null"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value)
        return "'" + text.replace("'", "''") + "'"

    @classmethod
    def render_yaml(cls, value: Any, indent: int = 0) -> list[str]:
        prefix = " " * indent
        lines: list[str] = []

        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                if isinstance(item, (dict, list)):
                    lines.append(f"{prefix}{key_text}:")
                    if isinstance(item, list) and not item:
                        lines[-1] = f"{prefix}{key_text}: []"
                    else:
                        lines.extend(cls.render_yaml(item, indent + 2))
                else:
                    lines.append(f"{prefix}{key_text}: {cls.yaml_scalar(item)}")
            return lines

        if isinstance(value, list):
            if not value:
                lines.append(f"{prefix}[]")
                return lines
            for item in value:
                if isinstance(item, (dict, list)):
                    lines.append(f"{prefix}-")
                    lines.extend(cls.render_yaml(item, indent + 2))
                else:
                    lines.append(f"{prefix}- {cls.yaml_scalar(item)}")
            return lines

        lines.append(f"{prefix}{cls.yaml_scalar(value)}")
        return lines

    # ------------------------------------------------------------------
    # Homepage — delegates to services.apps.homepage
    # ------------------------------------------------------------------

    def ensure_homepage_services_config(self, cfg: dict[str, Any], config_root: str) -> bool:
        import importlib
        from media_stack.core.service_registry.registry import SERVICES
        # Find management services and try to load the one with a HomepageService
        HomepageService = None
        for svc in SERVICES:
            if svc.category != "management":
                continue
            try:
                mod = importlib.import_module(f"media_stack.services.apps.{svc.id}.service")
                if hasattr(mod, "HomepageService"):
                    HomepageService = mod.HomepageService
                    break
            except ImportError:
                continue
        if HomepageService is None:
            return False

        return HomepageService(
            bool_cfg=self.bool_cfg,
            coerce_list=self.coerce_list,
            resolve_path=self.resolve_path,
            log=self.log,
            default_hosts=list(self.default_homepage_hosts),
            render_services_yaml=self.render_homepage_services_yaml,
        ).ensure_services_config(cfg, config_root)

    # ------------------------------------------------------------------
    # Jellyfin Auto Collections — delegates to services.apps.jellyfin
    # ------------------------------------------------------------------

    def _jellyfin_auto_collections_service(self):
        import importlib
        from media_stack.core.service_registry.registry import SERVICES
        ms_id = next((s.id for s in SERVICES if s.category == "media"), "")
        if not ms_id:
            return None
        _mod = importlib.import_module(f"media_stack.services.apps.{ms_id}.auto_collections")
        JellyfinAutoCollectionsService = _mod.JellyfinAutoCollectionsService

        return JellyfinAutoCollectionsService(
            bool_cfg=self.bool_cfg,
            resolve_path=self.resolve_path,
            normalize_url=self.normalize_url,
            wait_for_service=self.wait_for_service,
            resolve_jellyfin_api_key=self.resolve_jellyfin_api_key,
            jellyfin_request=self.jellyfin_request,
            log=self.log,
            render_yaml=self.render_yaml,
        )

    def detect_jellyfin_user_id(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        preferred_username: str,
    ) -> str:
        return self._jellyfin_auto_collections_service().detect_jellyfin_user_id(
            jellyfin_url, jellyfin_api_key, preferred_username
        )

    @staticmethod
    def default_auto_collections_plugins() -> dict[str, Any]:
        import importlib
        from media_stack.core.service_registry.registry import SERVICES
        ms_id = next((s.id for s in SERVICES if s.category == "media"), "")
        if not ms_id:
            return {}
        _mod = importlib.import_module(f"media_stack.services.apps.{ms_id}.auto_collections")
        return _mod.JellyfinAutoCollectionsService.default_auto_collections_plugins()

    def ensure_jellyfin_auto_collections_config(
        self,
        cfg: dict[str, Any],
        config_root: str,
        wait_timeout: int,
        resolve_jellyfin_user_id_value_fn: Callable[[dict[str, Any], str, str], str],
    ) -> None:
        self._jellyfin_auto_collections_service().ensure_config(
            cfg=cfg,
            config_root=config_root,
            wait_timeout=wait_timeout,
            resolve_jellyfin_user_id_value_fn=resolve_jellyfin_user_id_value_fn,
        )

    # ------------------------------------------------------------------
    # Maintainerr policy — delegates to services.apps.maintainerr
    # ------------------------------------------------------------------

    @staticmethod
    def deep_merge_objects(
        base_obj: dict[str, Any], override_obj: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(base_obj, dict):
            base_obj = {}
        if not isinstance(override_obj, dict):
            return json.loads(json.dumps(base_obj))

        out = json.loads(json.dumps(base_obj))
        for key, value in override_obj.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = ConfigArtifactsService.deep_merge_objects(out.get(key), value)
            else:
                out[key] = json.loads(json.dumps(value))
        return out

    @staticmethod
    def _repo_bootstrap_defaults_dir() -> Path:
        return Path(__file__).resolve().parents[1] / "contracts"

    def ensure_maintainerr_policy(self, cfg: dict[str, Any], config_root: str) -> None:
        import importlib
        from media_stack.core.service_registry.registry import SERVICES
        # Find the media management/policy service by checking for maintainerr-like capabilities
        policy_svc_id = next((s.id for s in SERVICES if s.category == "management" and s.port == 6246), "")
        if not policy_svc_id:
            return
        MaintainerrPolicyService = importlib.import_module(
            f"media_stack.services.apps.{policy_svc_id}.policy_service"
        ).MaintainerrPolicyService

        MaintainerrPolicyService(
            bool_cfg=self.bool_cfg,
            coerce_list=self.coerce_list,
            resolve_path=self.resolve_path,
            log=self.log,
            load_bootstrap_default_json=self.load_bootstrap_default_json,
            deep_merge_objects=self.deep_merge_objects,
        ).ensure_policy(cfg, config_root)
