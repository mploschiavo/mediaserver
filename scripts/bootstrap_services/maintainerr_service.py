"""Maintainerr integration orchestration service."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from bootstrap_services.apps.maintainerr.rule_sync_service import (
    MaintainerrRuleSyncDependencies,
    MaintainerrRuleSyncService,
)

HttpRequestFn = Callable[..., tuple[int, Any, str]]
LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ReadApiKeyFn = Callable[[str, str], str]
ReadJellyseerrApiKeyFn = Callable[[str, int], str]
GetArrAppFn = Callable[[list[dict[str, Any]], str], dict[str, Any] | None]
ResolvePathFn = Callable[[str, str], Any]


@dataclass
class MaintainerrService:
    log: LogFn
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    http_request: HttpRequestFn
    read_api_key: ReadApiKeyFn
    read_jellyseerr_api_key: ReadJellyseerrApiKeyFn
    get_arr_app: GetArrAppFn
    resolve_path: ResolvePathFn

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _token(value: Any) -> str:
        return str(value or "").strip().lower()

    def _ensure_enabled(self, cfg: dict[str, Any], key: str, default: bool = True) -> bool:
        return self.bool_cfg(cfg, key, default)

    def _service_section(self, integrations_cfg: dict[str, Any], name: str) -> dict[str, Any]:
        section = integrations_cfg.get(name) or {}
        return section if isinstance(section, dict) else {}

    def _request(
        self,
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, Any, str]:
        return self.http_request(
            base_url,
            path,
            method=method,
            payload=payload,
            timeout=30,
        )

    def _resolve_servarr_key(
        self,
        *,
        config_root: str,
        app_name: str,
        section_cfg: dict[str, Any],
        default_env: str,
    ) -> str:
        env_name = self._text(section_cfg.get("api_key_env") or default_env) or default_env
        env_value = self._text(os.environ.get(env_name))
        if env_value:
            self.log(f"[OK] Maintainerr: using {app_name} API key from env {env_name}")
            return env_value
        key = self._text(self.read_api_key(config_root, app_name))
        if not key:
            raise RuntimeError(
                f"Maintainerr: {app_name} API key is required but could not be resolved."
            )
        return key

    def _resolve_jellyseerr_key(
        self,
        *,
        config_root: str,
        wait_timeout: int,
        section_cfg: dict[str, Any],
    ) -> str:
        env_name = self._text(section_cfg.get("api_key_env") or "JELLYSEERR_API_KEY")
        env_value = self._text(os.environ.get(env_name))
        if env_value:
            self.log(f"[OK] Maintainerr: using Jellyseerr API key from env {env_name}")
            return env_value
        key = self._text(self.read_jellyseerr_api_key(config_root, wait_timeout))
        if not key:
            raise RuntimeError("Maintainerr: Jellyseerr API key is required but missing.")
        return key

    def _resolve_tautulli_key(self, *, config_root: str, section_cfg: dict[str, Any]) -> str:
        env_name = self._text(section_cfg.get("api_key_env") or "TAUTULLI_API_KEY")
        env_value = self._text(os.environ.get(env_name))
        if env_value:
            self.log(f"[OK] Maintainerr: using Tautulli API key from env {env_name}")
            return env_value

        ini_rel_path = self._text(section_cfg.get("api_key_config_path") or "tautulli/config.ini")
        ini_path = self.resolve_path(config_root, ini_rel_path)
        if not ini_path.exists():
            raise RuntimeError(
                f"Maintainerr: Tautulli API key is required and {ini_path} does not exist."
            )
        text = ini_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^\s*api_key\s*=\s*(\S+)\s*$", text, flags=re.MULTILINE)
        if not match:
            raise RuntimeError(
                f"Maintainerr: Tautulli API key is required and not present in {ini_path}."
            )
        return self._text(match.group(1))

    def _resolve_url(self, section_cfg: dict[str, Any], default_url: str) -> str:
        url = self._text(section_cfg.get("url") or default_url)
        if not url:
            raise RuntimeError("Maintainerr: integration URL is missing.")
        return self.normalize_url(url)

    def _rule_sync_service(self) -> MaintainerrRuleSyncService:
        return MaintainerrRuleSyncService(
            deps=MaintainerrRuleSyncDependencies(
                log=self.log,
                request=self._request,
                resolve_path=self.resolve_path,
            )
        )

    def _sync_policy_rules(
        self,
        *,
        maintainerr_url: str,
        maintainerr_cfg: dict[str, Any],
        config_root: str,
    ) -> None:
        self._rule_sync_service().sync_policy_rules(
            maintainerr_url=maintainerr_url,
            maintainerr_cfg=maintainerr_cfg,
            config_root=config_root,
        )

    def _test_connection(
        self,
        maintainerr_url: str,
        integration_name: str,
        payload: dict[str, Any],
        *,
        enabled: bool,
    ) -> None:
        if not enabled:
            return
        status, _, body = self._request(
            maintainerr_url,
            f"/api/settings/test/{integration_name}",
            method="POST",
            payload=payload,
        )
        if status < 200 or status >= 300:
            raise RuntimeError(
                f"Maintainerr: {integration_name} test failed (HTTP {status}): {body}"
            )
        self.log(f"[OK] Maintainerr: {integration_name} connection test passed")

    def _find_matching_servarr_entry(
        self,
        entries: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        desired_server_name = self._token(payload.get("serverName"))
        desired_url = self._token(payload.get("url"))
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            server_name = self._token(entry.get("serverName"))
            entry_url = self._token(entry.get("url"))
            if desired_server_name and server_name == desired_server_name:
                return entry
            if desired_url and entry_url == desired_url:
                return entry
        return None

    def _ensure_servarr_integration(
        self,
        maintainerr_url: str,
        integration_name: str,
        payload: dict[str, Any],
        *,
        test_connections: bool,
    ) -> None:
        endpoint = f"/api/settings/{integration_name}"
        status, data, body = self._request(maintainerr_url, endpoint)
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(
                f"Maintainerr: failed reading {integration_name} settings (HTTP {status}): {body}"
            )

        current = self._find_matching_servarr_entry(data, payload)
        desired_url = self._token(payload.get("url"))
        desired_name = self._token(payload.get("serverName"))
        desired_key = self._text(payload.get("apiKey"))
        needs_update = True
        if isinstance(current, dict):
            cur_name = self._token(current.get("serverName"))
            cur_url = self._token(current.get("url"))
            cur_key = self._text(current.get("apiKey"))
            needs_update = not (
                cur_name == desired_name and cur_url == desired_url and cur_key == desired_key
            )

        if needs_update:
            status, _, body = self._request(
                maintainerr_url,
                endpoint,
                method="POST",
                payload=payload,
            )
            if status < 200 or status >= 300:
                raise RuntimeError(
                    f"Maintainerr: failed saving {integration_name} settings (HTTP {status}): {body}"
                )
            self.log(f"[OK] Maintainerr: configured {integration_name} integration")
        else:
            self.log(f"[OK] Maintainerr: {integration_name} integration already configured")

        self._test_connection(
            maintainerr_url,
            integration_name,
            payload,
            enabled=test_connections,
        )

    def _ensure_single_endpoint_integration(
        self,
        maintainerr_url: str,
        integration_name: str,
        payload: dict[str, Any],
        *,
        test_connections: bool,
    ) -> None:
        endpoint = f"/api/settings/{integration_name}"
        status, data, body = self._request(maintainerr_url, endpoint)
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(
                f"Maintainerr: failed reading {integration_name} settings (HTTP {status}): {body}"
            )

        needs_update = not (
            self._token(data.get("url")) == self._token(payload.get("url"))
            and self._text(data.get("api_key")) == self._text(payload.get("api_key"))
        )
        if needs_update:
            status, _, body = self._request(
                maintainerr_url,
                endpoint,
                method="POST",
                payload=payload,
            )
            if status < 200 or status >= 300:
                raise RuntimeError(
                    f"Maintainerr: failed saving {integration_name} settings (HTTP {status}): {body}"
                )
            self.log(f"[OK] Maintainerr: configured {integration_name} integration")
        else:
            self.log(f"[OK] Maintainerr: {integration_name} integration already configured")

        self._test_connection(
            maintainerr_url,
            integration_name,
            payload,
            enabled=test_connections,
        )

    def _ensure_main_settings(
        self,
        maintainerr_url: str,
        *,
        cfg: dict[str, Any],
        maintainerr_cfg: dict[str, Any],
        integrations_cfg: dict[str, Any],
        config_root: str,
        wait_timeout: int,
    ) -> None:
        main_section = self._service_section(integrations_cfg, "main")
        if not self._ensure_enabled(main_section, "enabled", True):
            return

        status, current, body = self._request(maintainerr_url, "/api/settings")
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError(
                f"Maintainerr: failed reading main settings (HTTP {status}): {body}"
            )

        desired = dict(current)

        application_url = self._text(
            main_section.get("application_url")
            or maintainerr_cfg.get("application_url")
            or maintainerr_cfg.get("external_url")
            or os.environ.get("MAINTAINERR_APPLICATION_URL")
            or "maintainerr.local"
        )
        if application_url:
            desired["applicationUrl"] = application_url

        media_server_type = self._text(
            main_section.get("media_server_type")
            or desired.get("media_server_type")
            or "jellyfin"
        ).lower()
        if media_server_type:
            desired["media_server_type"] = media_server_type

        jellyseerr_section = self._service_section(integrations_cfg, "jellyseerr")
        jellyseerr_cfg = cfg.get("jellyseerr") or {}
        desired["seerr_url"] = self._resolve_url(
            jellyseerr_section,
            self._text(jellyseerr_cfg.get("url") or "http://jellyseerr:5055"),
        )
        if self._ensure_enabled(jellyseerr_section, "enabled", True):
            desired["seerr_api_key"] = self._resolve_jellyseerr_key(
                config_root=config_root,
                wait_timeout=wait_timeout,
                section_cfg=jellyseerr_section,
            )

        jellyfin_cfg = cfg.get("jellyfin") or {}
        desired["jellyfin_url"] = self._resolve_url(
            main_section,
            self._text(main_section.get("jellyfin_url") or jellyfin_cfg.get("url") or "http://jellyfin:8096"),
        )
        desired["jellyfin_server_name"] = self._text(
            main_section.get("jellyfin_server_name")
            or desired.get("jellyfin_server_name")
            or "Jellyfin"
        )

        jellyfin_api_env = self._text(main_section.get("jellyfin_api_key_env") or "JELLYFIN_API_KEY")
        jellyfin_api_key = self._text(os.environ.get(jellyfin_api_env))
        if jellyfin_api_key:
            desired["jellyfin_api_key"] = jellyfin_api_key

        jellyfin_user_env = self._text(main_section.get("jellyfin_user_id_env") or "JELLYFIN_USER_ID")
        jellyfin_user_id = self._text(os.environ.get(jellyfin_user_env))
        if jellyfin_user_id:
            desired["jellyfin_user_id"] = jellyfin_user_id

        tautulli_section = self._service_section(integrations_cfg, "tautulli")
        if self._ensure_enabled(tautulli_section, "enabled", True):
            desired["tautulli_url"] = self._resolve_url(
                tautulli_section,
                self._text(
                    (cfg.get("tautulli") or {}).get("url") or "http://tautulli:8181"
                ),
            )
            desired["tautulli_api_key"] = self._resolve_tautulli_key(
                config_root=config_root,
                section_cfg=tautulli_section,
            )

        watched_fields = [
            "applicationUrl",
            "media_server_type",
            "seerr_url",
            "jellyfin_url",
            "jellyfin_api_key",
            "jellyfin_user_id",
            "jellyfin_server_name",
            "seerr_api_key",
            "tautulli_url",
            "tautulli_api_key",
        ]
        needs_update = any(self._text(current.get(field)) != self._text(desired.get(field)) for field in watched_fields)
        if not needs_update:
            self.log("[OK] Maintainerr: main settings already configured")
            return

        status, _, body = self._request(
            maintainerr_url,
            "/api/settings",
            method="POST",
            payload=desired,
        )
        if status < 200 or status >= 300:
            raise RuntimeError(
                f"Maintainerr: failed saving main settings (HTTP {status}): {body}"
            )
        self.log("[OK] Maintainerr: configured main settings")

    def ensure_integrations(
        self,
        cfg: dict[str, Any],
        config_root: str,
        arr_apps: list[dict[str, Any]],
        wait_timeout: int,
    ) -> None:
        maintainerr_cfg = cfg.get("maintainerr") or {}
        if not self.bool_cfg(maintainerr_cfg, "enabled", False):
            return

        integrations_cfg = maintainerr_cfg.get("integrations") or {}
        if not isinstance(integrations_cfg, dict):
            raise RuntimeError("Maintainerr: maintainerr.integrations must be an object.")
        if not self._ensure_enabled(integrations_cfg, "enabled", True):
            return

        maintainerr_url = self._resolve_url(
            integrations_cfg,
            self._text(maintainerr_cfg.get("url") or "http://maintainerr:6246"),
        )
        self.wait_for_service("Maintainerr", maintainerr_url, "/api/settings", wait_timeout)
        test_connections = self._ensure_enabled(integrations_cfg, "test_connections", True)

        self._ensure_main_settings(
            maintainerr_url,
            cfg=cfg,
            maintainerr_cfg=maintainerr_cfg,
            integrations_cfg=integrations_cfg,
            config_root=config_root,
            wait_timeout=wait_timeout,
        )

        radarr_section = self._service_section(integrations_cfg, "radarr")
        if self._ensure_enabled(radarr_section, "enabled", True):
            radarr_app = self.get_arr_app(arr_apps, "radarr")
            radarr_url = self._resolve_url(
                radarr_section,
                self._text((radarr_app or {}).get("url") or "http://radarr:7878"),
            )
            radarr_payload = {
                "serverName": self._text(
                    radarr_section.get("server_name")
                    or (radarr_app or {}).get("name")
                    or "Radarr"
                ),
                "url": radarr_url,
                "apiKey": self._resolve_servarr_key(
                    config_root=config_root,
                    app_name="radarr",
                    section_cfg=radarr_section,
                    default_env="RADARR_API_KEY",
                ),
            }
            self._ensure_servarr_integration(
                maintainerr_url,
                "radarr",
                radarr_payload,
                test_connections=test_connections,
            )

        sonarr_section = self._service_section(integrations_cfg, "sonarr")
        if self._ensure_enabled(sonarr_section, "enabled", True):
            sonarr_app = self.get_arr_app(arr_apps, "sonarr")
            sonarr_url = self._resolve_url(
                sonarr_section,
                self._text((sonarr_app or {}).get("url") or "http://sonarr:8989"),
            )
            sonarr_payload = {
                "serverName": self._text(
                    sonarr_section.get("server_name")
                    or (sonarr_app or {}).get("name")
                    or "Sonarr"
                ),
                "url": sonarr_url,
                "apiKey": self._resolve_servarr_key(
                    config_root=config_root,
                    app_name="sonarr",
                    section_cfg=sonarr_section,
                    default_env="SONARR_API_KEY",
                ),
            }
            self._ensure_servarr_integration(
                maintainerr_url,
                "sonarr",
                sonarr_payload,
                test_connections=test_connections,
            )

        jellyseerr_section = self._service_section(integrations_cfg, "jellyseerr")
        if self._ensure_enabled(jellyseerr_section, "enabled", True):
            jellyseerr_cfg = cfg.get("jellyseerr") or {}
            jellyseerr_payload = {
                "url": self._resolve_url(
                    jellyseerr_section,
                    self._text(jellyseerr_cfg.get("url") or "http://jellyseerr:5055"),
                ),
                "api_key": self._resolve_jellyseerr_key(
                    config_root=config_root,
                    wait_timeout=wait_timeout,
                    section_cfg=jellyseerr_section,
                ),
            }
            self._ensure_single_endpoint_integration(
                maintainerr_url,
                "seerr",
                jellyseerr_payload,
                test_connections=test_connections,
            )

        tautulli_section = self._service_section(integrations_cfg, "tautulli")
        if self._ensure_enabled(tautulli_section, "enabled", True):
            tautulli_payload = {
                "url": self._resolve_url(
                    tautulli_section,
                    self._text(
                        (cfg.get("tautulli") or {}).get("url") or "http://tautulli:8181"
                    ),
                ),
                "api_key": self._resolve_tautulli_key(
                    config_root=config_root,
                    section_cfg=tautulli_section,
                ),
            }
            self._ensure_single_endpoint_integration(
                maintainerr_url,
                "tautulli",
                tautulli_payload,
                test_connections=test_connections,
            )

        if self._ensure_enabled(integrations_cfg, "sync_rules", True):
            self._sync_policy_rules(
                maintainerr_url=maintainerr_url,
                maintainerr_cfg=maintainerr_cfg,
                config_root=config_root,
            )

        self.log("[OK] Maintainerr: integration reconcile complete")
