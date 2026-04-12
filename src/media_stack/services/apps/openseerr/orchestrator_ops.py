"""OpenSeerr orchestration entrypoints."""

from __future__ import annotations

from typing import Any

from .api_ops import (
    ensure_jellyfin_settings,
    ensure_main_settings,
    ensure_radarr,
    ensure_sonarr,
)
from .file_ops import configure_via_settings_file


class OpenSeerrOrchestratorOps:

    @staticmethod
    def _request_manager_cfg(cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        openseerr_cfg = cfg.get("openseerr")
        if isinstance(openseerr_cfg, dict):
            return "OpenSeerr", openseerr_cfg
        jelly_cfg = cfg.get("jellyseerr")
        if isinstance(jelly_cfg, dict):
            return "Jellyseerr", jelly_cfg
        return "OpenSeerr", {}

    def permission_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "(http 403)" in text
            or "permission to access this endpoint" in text
            or "you do not have permission" in text
        )

    def configure(self, 
        svc,
        cfg: dict[str, Any],
        arr_apps: list[dict[str, Any]],
        app_keys: dict[str, str],
        config_root: str,
        wait_timeout: int,
    ) -> None:
        service_label, jelly_cfg = _request_manager_cfg(cfg)
        if not svc.bool_cfg(jelly_cfg, "enabled", False):
            return

        default_url = (
            "http://openseerr:5055" if service_label == "OpenSeerr" else "http://jellyseerr:5055"
        )
        jellyseerr_url = svc.normalize_url(jelly_cfg.get("url", default_url))
        svc.wait_for_service(service_label, jellyseerr_url, "/api/v1/status", wait_timeout)

        jellyseerr_key = svc.read_jellyseerr_api_key(config_root, wait_timeout)
        radarr_app = svc.get_arr_app(arr_apps, "Radarr")
        sonarr_app = svc.get_arr_app(arr_apps, "Sonarr")
        enforced_file_bootstrap = False

        try:
            ensure_main_settings(svc, jellyseerr_url, jellyseerr_key, jelly_cfg)
            ensure_jellyfin_settings(svc, jellyseerr_url, jellyseerr_key, jelly_cfg, config_root)

            if radarr_app and "Radarr" in app_keys:
                ensure_radarr(
                    svc,
                    jellyseerr_url,
                    jellyseerr_key,
                    radarr_app,
                    app_keys["Radarr"],
                    jelly_cfg,
                )
            else:
                svc.log(
                    f"[WARN] {service_label}: Radarr app config not found; skipping Radarr mapping."
                )

            if sonarr_app and "Sonarr" in app_keys:
                ensure_sonarr(
                    svc,
                    jellyseerr_url,
                    jellyseerr_key,
                    sonarr_app,
                    app_keys["Sonarr"],
                    jelly_cfg,
                )
            else:
                svc.log(
                    f"[WARN] {service_label}: Sonarr app config not found; skipping Sonarr mapping."
                )
        except Exception as exc:
            if not permission_error(exc):
                raise
            svc.log(
                f"[WARN] {service_label} API bootstrap hit permission gate; "
                "applying settings-file bootstrap fallback."
            )
            configure_via_settings_file(svc, cfg, arr_apps, app_keys, config_root)
            enforced_file_bootstrap = True

        if svc.bool_cfg(jelly_cfg, "enforce_settings_file", True) and not enforced_file_bootstrap:
            configure_via_settings_file(svc, cfg, arr_apps, app_keys, config_root)


_instance = OpenSeerrOrchestratorOps()
permission_error = _instance.permission_error
configure = _instance.configure
