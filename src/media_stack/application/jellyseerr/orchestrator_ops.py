"""Jellyseerr orchestration entrypoints."""

from __future__ import annotations

from typing import Any

from media_stack.infrastructure.jellyseerr.api_ops import (
    ensure_jellyfin_settings,
    ensure_main_settings,
    ensure_radarr,
    ensure_sonarr,
)
from media_stack.infrastructure.jellyseerr.file_ops import configure_via_settings_file
from media_stack.infrastructure.jellyseerr.local_admin_ops import ensure_local_admin_user
from media_stack.api.services.registry import service_internal_url


class JellyseerrOrchestratorOps:

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
        jelly_cfg = cfg.get("jellyseerr") or {}
        if not svc.bool_cfg(jelly_cfg, "enabled", False):
            return

        jellyseerr_url = svc.normalize_url(jelly_cfg.get("url", service_internal_url("jellyseerr")))
        app_auth = cfg.get("app_auth") if isinstance(cfg.get("app_auth"), dict) else {}
        path_base = (
            (app_auth.get("path_prefix_url_base_by_app") or {}).get("jellyseerr")
            or (app_auth.get("url_base_by_app") or {}).get("jellyseerr")
            or ""
        )
        path_base = svc.normalize_base_path(str(path_base or ""))
        if path_base:
            jellyseerr_url = svc.normalize_url(jellyseerr_url.rstrip("/") + path_base)
        svc.wait_for_service("Jellyseerr", jellyseerr_url, "/api/v1/status", wait_timeout)
        # Seed local admin as soon as Jellyseerr is reachable so login survives
        # downstream integration/bootstrap failures.
        ensure_local_admin_user(svc, cfg, config_root)

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
                svc.log("[WARN] Jellyseerr: Radarr app config not found; skipping Radarr mapping.")

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
                svc.log("[WARN] Jellyseerr: Sonarr app config not found; skipping Sonarr mapping.")
        except Exception as exc:
            if not permission_error(exc):
                raise
            svc.log(
                "[WARN] Jellyseerr API bootstrap hit permission gate; "
                "applying settings-file bootstrap fallback."
            )
            configure_via_settings_file(svc, cfg, arr_apps, app_keys, config_root)
            enforced_file_bootstrap = True

        if svc.bool_cfg(jelly_cfg, "enforce_settings_file", True) and not enforced_file_bootstrap:
            configure_via_settings_file(svc, cfg, arr_apps, app_keys, config_root)


_instance = JellyseerrOrchestratorOps()
permission_error = _instance.permission_error
configure = _instance.configure
