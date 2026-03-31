"""Writers for bootstrap-generated config artifacts."""

from __future__ import annotations

import json
import os
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

    def ensure_homepage_services_config(self, cfg: dict[str, Any], config_root: str) -> bool:
        homepage_cfg = cfg.get("homepage") or {}
        hosts = [
            str(h).strip().lower()
            for h in self.coerce_list(homepage_cfg.get("hosts"))
            if str(h).strip()
        ]
        enabled = self.bool_cfg(homepage_cfg, "enabled", False) or bool(hosts)
        if not enabled:
            return False

        scheme = str(homepage_cfg.get("scheme", "http")).strip().lower() or "http"
        services_rel_path = str(
            homepage_cfg.get("services_relative_path") or "homepage/services.yaml"
        ).strip()
        services_path = self.resolve_path(config_root, services_rel_path)
        services_path.parent.mkdir(parents=True, exist_ok=True)

        if not hosts:
            hosts = list(self.default_homepage_hosts)

        onboarding_cfg = homepage_cfg.get("device_onboarding")
        if not isinstance(onboarding_cfg, dict):
            onboarding_cfg = {}
        rendered = self.render_homepage_services_yaml(
            hosts,
            scheme=scheme,
            onboarding=onboarding_cfg,
        )
        current = (
            services_path.read_text(encoding="utf-8", errors="replace")
            if services_path.exists()
            else ""
        )
        if current == rendered:
            self.log(f"[OK] Homepage: services config already up-to-date at {services_path}")
            return False

        services_path.write_text(rendered, encoding="utf-8")
        self.log(f"[OK] Homepage: wrote services config {services_path} (hosts={len(hosts)})")
        self.log("[INFO] Homepage: restart recommended to pick up updated services config.")
        return True

    def detect_jellyfin_user_id(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        preferred_username: str,
    ) -> str:
        status, users, body = self.jellyfin_request(jellyfin_url, "/Users", jellyfin_api_key)
        if status != 200 or not isinstance(users, list):
            raise RuntimeError(
                f"Jellyfin Auto Collections: failed listing users (HTTP {status}): {body}"
            )

        preferred = str(preferred_username or "").strip().lower()
        if preferred:
            for user in users:
                if not isinstance(user, dict):
                    continue
                if str(user.get("Name") or "").strip().lower() == preferred:
                    candidate = str(user.get("Id") or "").strip()
                    if candidate:
                        return candidate

        for user in users:
            if not isinstance(user, dict):
                continue
            policy = user.get("Policy") or {}
            if bool(policy.get("IsAdministrator", False)):
                candidate = str(user.get("Id") or "").strip()
                if candidate:
                    return candidate

        for user in users:
            if not isinstance(user, dict):
                continue
            candidate = str(user.get("Id") or "").strip()
            if candidate:
                return candidate

        return ""

    @staticmethod
    def default_auto_collections_plugins() -> dict[str, Any]:
        return {"jellyfin_api": {"enabled": False, "list_ids": []}}

    def ensure_jellyfin_auto_collections_config(
        self,
        cfg: dict[str, Any],
        config_root: str,
        wait_timeout: int,
        resolve_jellyfin_user_id_value_fn: Callable[[dict[str, Any], str, str], str],
    ) -> None:
        auto_cfg = cfg.get("jellyfin_auto_collections") or {}
        if not self.bool_cfg(auto_cfg, "enabled", False):
            return

        jellyfin_url = self.normalize_url(auto_cfg.get("url", "http://jellyfin:8096"))
        self.wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

        jellyfin_api_key = self.resolve_jellyfin_api_key(auto_cfg, config_root)
        if not jellyfin_api_key:
            raise RuntimeError(
                "Jellyfin Auto Collections: API key unavailable. Set JELLYFIN_API_KEY or keep "
                "jellyfin_auto_collections.auto_discover_api_key_from_db=true."
            )

        user_id = resolve_jellyfin_user_id_value_fn(auto_cfg, jellyfin_url, jellyfin_api_key)

        if not user_id and self.bool_cfg(auto_cfg, "required_user_id", False):
            raise RuntimeError("Jellyfin Auto Collections: no Jellyfin user id could be resolved.")
        if not user_id:
            self.log(
                "[WARN] Jellyfin Auto Collections: could not resolve Jellyfin user id. "
                "Config will be written with an empty fallback user id."
            )

        plugins_cfg = auto_cfg.get("plugins")
        if not isinstance(plugins_cfg, dict) or not plugins_cfg:
            plugins_cfg = self.default_auto_collections_plugins()

        timezone_value = str(auto_cfg.get("timezone") or os.environ.get("TZ") or "UTC").strip()
        crontab_value = str(auto_cfg.get("crontab") or "0 */6 * * *").strip()

        config_data = {
            "crontab": crontab_value,
            "timezone": timezone_value,
            "jellyfin": {
                "server_url": jellyfin_url,
                "api_key": jellyfin_api_key,
                "user_id": user_id,
            },
            "plugins": plugins_cfg,
        }

        config_rel_path = str(
            auto_cfg.get("config_relative_path") or "jellyfin-auto-collections/config.yaml"
        ).strip()
        config_path = self.resolve_path(config_root, config_rel_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_yaml = "\n".join(self.render_yaml(config_data)) + "\n"

        existing = (
            config_path.read_text(encoding="utf-8", errors="replace")
            if config_path.exists()
            else ""
        )
        if existing == config_yaml:
            self.log(f"[OK] Jellyfin Auto Collections: config already up-to-date at {config_path}")
            return

        config_path.write_text(config_yaml, encoding="utf-8")
        self.log(f"[OK] Jellyfin Auto Collections: wrote config {config_path}")

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

    def ensure_maintainerr_policy(self, cfg: dict[str, Any], config_root: str) -> None:
        maintainerr_cfg = cfg.get("maintainerr") or {}
        if not self.bool_cfg(maintainerr_cfg, "enabled", False):
            return

        default_policy = self.load_bootstrap_default_json(
            "maintainerr_policy.json",
            {
                "version": 1,
                "retention": {},
                "rules": [],
            },
        )
        desired = self.deep_merge_objects(default_policy, maintainerr_cfg.get("policy") or {})

        relative_path = str(
            maintainerr_cfg.get("policy_relative_path") or "maintainerr/policy.json"
        ).strip()
        policy_path = self.resolve_path(config_root, relative_path)
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(desired, ensure_ascii=True, indent=2, sort_keys=True) + "\n"

        if policy_path.exists():
            current = policy_path.read_text(encoding="utf-8", errors="replace")
            if current == rendered:
                self.log(f"[OK] Maintainerr policy: already up-to-date at {policy_path}")
                return

        policy_path.write_text(rendered, encoding="utf-8")
        self.log(f"[OK] Maintainerr policy: wrote {policy_path}")
