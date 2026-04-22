"""Jellyfin Auto Collections config artifact generation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from media_stack.api.services.registry import service_internal_url

BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
ResolvePathFn = Callable[[str | Path, str], Path]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ResolveJellyfinApiKeyFn = Callable[[dict[str, Any], str], str]
JellyfinRequestFn = Callable[[str, str, str, str, Any, int], tuple[int, Any, str]]
LogFn = Callable[[str], None]
RenderYamlFn = Callable[[Any, int], list[str]]


@dataclass
class JellyfinAutoCollectionsService:
    bool_cfg: BoolCfgFn
    resolve_path: ResolvePathFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    resolve_jellyfin_api_key: ResolveJellyfinApiKeyFn
    jellyfin_request: JellyfinRequestFn
    log: LogFn
    render_yaml: RenderYamlFn

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

    def ensure_config(
        self,
        cfg: dict[str, Any],
        config_root: str,
        wait_timeout: int,
        resolve_jellyfin_user_id_value_fn: Callable[[dict[str, Any], str, str], str],
    ) -> None:
        auto_cfg = cfg.get("jellyfin_auto_collections") or {}
        if not self.bool_cfg(auto_cfg, "enabled", False):
            return

        jellyfin_url = self.normalize_url(auto_cfg.get("url", service_internal_url("jellyfin")))
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
        config_yaml = "\n".join(self.render_yaml(config_data, 0)) + "\n"

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
