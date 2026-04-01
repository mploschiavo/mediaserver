"""Settings-file bootstrap operations for OpenSeerr."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _request_manager_cfg(cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    openseerr_cfg = cfg.get("openseerr")
    if isinstance(openseerr_cfg, dict):
        return "OpenSeerr", openseerr_cfg
    jelly_cfg = cfg.get("jellyseerr")
    if isinstance(jelly_cfg, dict):
        return "Jellyseerr", jelly_cfg
    return "OpenSeerr", {}


def _settings_path_for(service_label: str, config_root: str) -> Path:
    primary_dir = "openseerr" if service_label == "OpenSeerr" else "jellyseerr"
    primary = Path(config_root) / primary_dir / "settings.json"
    if primary.exists():
        return primary
    alternate_dir = "jellyseerr" if primary_dir == "openseerr" else "openseerr"
    alternate = Path(config_root) / alternate_dir / "settings.json"
    if alternate.exists():
        return alternate
    return primary


def configure_via_settings_file(
    svc,
    cfg: dict[str, Any],
    arr_apps: list[dict[str, Any]],
    app_keys: dict[str, str],
    config_root: str,
) -> None:
    service_label, jelly_cfg = _request_manager_cfg(cfg)
    settings_path = _settings_path_for(service_label, config_root)
    settings = svc.read_json_file(settings_path)

    main_cfg = settings.setdefault("main", {})
    if svc.bool_cfg(jelly_cfg, "set_media_server_type_jellyfin", True):
        main_cfg["mediaServerType"] = 2
    settings.setdefault("public", {})["initialized"] = True

    jellyfin_cfg = jelly_cfg.get("jellyfin") or {}
    if svc.bool_cfg(jellyfin_cfg, "configure", False):
        jellyfin_api_key = svc.resolve_jellyfin_api_key(jellyfin_cfg, config_root)
        if not jellyfin_api_key:
            raise RuntimeError(
                f"{service_label} file bootstrap: jellyfin.configure=true but Jellyfin API key could not be resolved."
            )
        parsed_jf = svc.parse_service_url(
            jellyfin_cfg.get("url", "http://jellyfin:8096"), 8096
        )
        jf = settings.setdefault("jellyfin", {})
        jf["name"] = jellyfin_cfg.get("name", "Jellyfin")
        jf["ip"] = parsed_jf["hostname"]
        jf["port"] = parsed_jf["port"]
        jf["useSsl"] = parsed_jf["use_ssl"]
        jf["urlBase"] = parsed_jf["base_url"]
        jf["externalHostname"] = jellyfin_cfg.get("external_url", "")
        jf["jellyfinForgotPasswordUrl"] = jellyfin_cfg.get("forgot_password_url", "")
        jf["apiKey"] = jellyfin_api_key
        svc.log(f"[OK] {service_label}: wrote Jellyfin settings via file bootstrap")

    radarr_app = svc.get_arr_app(arr_apps, "Radarr")
    if (
        radarr_app
        and "Radarr" in app_keys
        and svc.bool_cfg((jelly_cfg.get("radarr") or {}), "enabled", True)
    ):
        radarr_cfg = jelly_cfg.get("radarr") or {}
        radarr_url = svc.normalize_url(radarr_app["url"])
        radarr_api_base = svc.detect_arr_api_base("Radarr", radarr_url, app_keys["Radarr"])
        radarr_profile_names = svc.coerce_list(
            radarr_cfg.get("quality_profile_preferred_names")
            or radarr_app.get("quality_profile_preferred_names")
            or []
        )
        radarr_profile = svc.get_arr_quality_profile(
            "Radarr",
            radarr_url,
            radarr_api_base,
            app_keys["Radarr"],
            preferred_id=radarr_cfg.get("active_profile_id"),
            preferred_names=radarr_profile_names,
        )
        radarr_root = svc.get_arr_root_folder_path(
            "Radarr",
            radarr_url,
            radarr_api_base,
            app_keys["Radarr"],
            radarr_app.get("root_folder"),
        )
        parsed_radarr = svc.parse_service_url(radarr_app["url"], 7878)
        settings["radarr"] = [
            {
                "name": radarr_cfg.get("name", "Radarr"),
                "hostname": parsed_radarr["hostname"],
                "port": parsed_radarr["port"],
                "apiKey": app_keys["Radarr"],
                "useSsl": parsed_radarr["use_ssl"],
                "baseUrl": parsed_radarr["base_url"],
                "activeProfileId": svc.to_int(radarr_profile.get("id"), 1),
                "activeProfileName": str(radarr_profile.get("name") or "Default"),
                "activeDirectory": radarr_root,
                "is4k": bool(radarr_cfg.get("is4k", False)),
                "minimumAvailability": str(radarr_cfg.get("minimum_availability", "released")),
                "isDefault": bool(radarr_cfg.get("is_default", True)),
                "externalUrl": radarr_cfg.get("external_url", ""),
                "syncEnabled": bool(radarr_cfg.get("sync_enabled", True)),
                "preventSearch": bool(radarr_cfg.get("prevent_search", False)),
            }
        ]
        svc.log(f"[OK] {service_label}: wrote Radarr settings via file bootstrap")

    sonarr_app = svc.get_arr_app(arr_apps, "Sonarr")
    if (
        sonarr_app
        and "Sonarr" in app_keys
        and svc.bool_cfg((jelly_cfg.get("sonarr") or {}), "enabled", True)
    ):
        sonarr_cfg = jelly_cfg.get("sonarr") or {}
        sonarr_url = svc.normalize_url(sonarr_app["url"])
        sonarr_api_base = svc.detect_arr_api_base("Sonarr", sonarr_url, app_keys["Sonarr"])
        sonarr_profile_names = svc.coerce_list(
            sonarr_cfg.get("quality_profile_preferred_names")
            or sonarr_app.get("quality_profile_preferred_names")
            or []
        )
        sonarr_profile = svc.get_arr_quality_profile(
            "Sonarr",
            sonarr_url,
            sonarr_api_base,
            app_keys["Sonarr"],
            preferred_id=sonarr_cfg.get("active_profile_id"),
            preferred_names=sonarr_profile_names,
        )
        sonarr_root = svc.get_arr_root_folder_path(
            "Sonarr",
            sonarr_url,
            sonarr_api_base,
            app_keys["Sonarr"],
            sonarr_app.get("root_folder"),
        )
        parsed_sonarr = svc.parse_service_url(sonarr_app["url"], 8989)
        settings["sonarr"] = [
            {
                "name": sonarr_cfg.get("name", "Sonarr"),
                "hostname": parsed_sonarr["hostname"],
                "port": parsed_sonarr["port"],
                "apiKey": app_keys["Sonarr"],
                "useSsl": parsed_sonarr["use_ssl"],
                "baseUrl": parsed_sonarr["base_url"],
                "activeProfileId": svc.to_int(sonarr_profile.get("id"), 1),
                "activeProfileName": str(sonarr_profile.get("name") or "Default"),
                "activeDirectory": sonarr_root,
                "activeLanguageProfileId": svc.get_sonarr_language_profile_id(
                    sonarr_url, sonarr_api_base, app_keys["Sonarr"]
                ),
                "is4k": bool(sonarr_cfg.get("is4k", False)),
                "enableSeasonFolders": bool(sonarr_cfg.get("enable_season_folders", True)),
                "isDefault": bool(sonarr_cfg.get("is_default", True)),
                "externalUrl": sonarr_cfg.get("external_url", ""),
                "syncEnabled": bool(sonarr_cfg.get("sync_enabled", True)),
                "preventSearch": bool(sonarr_cfg.get("prevent_search", False)),
            }
        ]
        svc.log(f"[OK] {service_label}: wrote Sonarr settings via file bootstrap")

    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    svc.log(f"[OK] {service_label}: settings file bootstrap applied")
