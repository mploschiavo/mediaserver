"""Jellyseerr bootstrap orchestration service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

HttpRequestFn = Callable[..., tuple[int, Any, str]]
LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ResolveJellyfinApiKeyFn = Callable[[dict[str, Any], str], str]
ParseServiceUrlFn = Callable[[str, int], dict[str, Any]]
ToIntFn = Callable[[Any, Any], Any]
CoerceListFn = Callable[[Any], list[Any]]
ChooseProfileFn = Callable[..., dict[str, Any] | None]
ChooseRootFolderFn = Callable[[list[dict[str, Any]], str], str]
NormalizeBasePathFn = Callable[[str], str]
FindExistingServarrFn = Callable[..., dict[str, Any] | None]
ReadJsonFileFn = Callable[[Path], Any]
GetArrAppFn = Callable[[list[dict[str, Any]], str], dict[str, Any] | None]
DetectArrApiBaseFn = Callable[[str, str, str], str]
GetArrQualityProfileFn = Callable[..., dict[str, Any]]
GetArrRootFolderPathFn = Callable[..., str]
GetSonarrLanguageProfileIdFn = Callable[[str, str, str], int]
ReadJellyseerrApiKeyFn = Callable[[str, int], str]


@dataclass
class JellyseerrService:
    log: LogFn
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    resolve_jellyfin_api_key: ResolveJellyfinApiKeyFn
    parse_service_url: ParseServiceUrlFn
    to_int: ToIntFn
    coerce_list: CoerceListFn
    choose_profile: ChooseProfileFn
    choose_root_folder: ChooseRootFolderFn
    normalize_base_path: NormalizeBasePathFn
    find_existing_servarr: FindExistingServarrFn
    read_json_file: ReadJsonFileFn
    get_arr_app: GetArrAppFn
    detect_arr_api_base: DetectArrApiBaseFn
    get_arr_quality_profile: GetArrQualityProfileFn
    get_arr_root_folder_path: GetArrRootFolderPathFn
    get_sonarr_language_profile_id: GetSonarrLanguageProfileIdFn
    read_jellyseerr_api_key: ReadJellyseerrApiKeyFn
    http_request: HttpRequestFn

    def ensure_main_settings(
        self,
        jellyseerr_url: str,
        jellyseerr_key: str,
        jelly_cfg: dict[str, Any],
    ) -> None:
        media_server_type = jelly_cfg.get("media_server_type")
        if media_server_type is None and self.bool_cfg(
            jelly_cfg, "set_media_server_type_jellyfin", True
        ):
            media_server_type = 2

        if media_server_type is None:
            return

        status, current, body = self.http_request(
            jellyseerr_url, "/api/v1/settings/main", api_key=jellyseerr_key
        )
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError(f"Jellyseerr: failed to read main settings (HTTP {status}): {body}")

        desired_type = int(media_server_type)
        if self.to_int(current.get("mediaServerType")) == desired_type:
            self.log(f"[OK] Jellyseerr: mediaServerType already set to {desired_type}")
            return

        status, _, body = self.http_request(
            jellyseerr_url,
            "/api/v1/settings/main",
            api_key=jellyseerr_key,
            method="POST",
            payload={"mediaServerType": desired_type},
        )
        if status in (200, 201, 202):
            self.log(f"[OK] Jellyseerr: set mediaServerType={desired_type}")
            return

        raise RuntimeError(f"Jellyseerr: failed to set mediaServerType (HTTP {status}): {body}")

    def ensure_jellyfin_settings(
        self,
        jellyseerr_url: str,
        jellyseerr_key: str,
        jelly_cfg: dict[str, Any],
        config_root: str,
    ) -> None:
        jellyfin_cfg = jelly_cfg.get("jellyfin") or {}
        if not self.bool_cfg(jellyfin_cfg, "configure", False):
            return

        jellyfin_api_key = self.resolve_jellyfin_api_key(jellyfin_cfg, config_root)
        if not jellyfin_api_key:
            raise RuntimeError(
                "Jellyseerr: jellyfin.configure=true but Jellyfin API key could not be resolved."
            )

        jellyfin_url = jellyfin_cfg.get("url", "http://jellyfin:8096")
        parsed = self.parse_service_url(jellyfin_url, 8096)
        payload = {
            "ip": parsed["hostname"],
            "port": parsed["port"],
            "useSsl": parsed["use_ssl"],
            "urlBase": parsed["base_url"],
            "apiKey": jellyfin_api_key,
            "externalHostname": jellyfin_cfg.get("external_url", ""),
            "jellyfinForgotPasswordUrl": jellyfin_cfg.get("forgot_password_url", ""),
        }

        status, _, body = self.http_request(
            jellyseerr_url,
            "/api/v1/settings/jellyfin",
            api_key=jellyseerr_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            self.log("[OK] Jellyseerr: configured Jellyfin connection")
            return

        raise RuntimeError(
            f"Jellyseerr: failed to configure Jellyfin settings (HTTP {status}): {body}"
        )

    def ensure_radarr(
        self,
        jellyseerr_url: str,
        jellyseerr_key: str,
        radarr_app_cfg: dict[str, Any],
        radarr_api_key: str,
        jelly_cfg: dict[str, Any],
    ) -> None:
        radarr_cfg = jelly_cfg.get("radarr") or {}
        if not self.bool_cfg(radarr_cfg, "enabled", True):
            return

        parsed = self.parse_service_url(radarr_app_cfg["url"], 7878)
        test_payload = {
            "hostname": parsed["hostname"],
            "port": parsed["port"],
            "apiKey": radarr_api_key,
            "useSsl": parsed["use_ssl"],
            "baseUrl": parsed["base_url"],
        }
        status, test_data, body = self.http_request(
            jellyseerr_url,
            "/api/v1/settings/radarr/test",
            api_key=jellyseerr_key,
            method="POST",
            payload=test_payload,
        )
        if status != 200 or not isinstance(test_data, dict):
            raise RuntimeError(f"Jellyseerr: Radarr connection test failed (HTTP {status}): {body}")

        profiles = test_data.get("profiles") or []
        if not profiles:
            raise RuntimeError("Jellyseerr: Radarr test returned no quality profiles.")
        selected_profile = self.choose_profile(
            profiles,
            preferred_id=radarr_cfg.get("active_profile_id"),
            preferred_names=self.coerce_list(
                radarr_cfg.get("quality_profile_preferred_names")
                or radarr_cfg.get("preferred_profile_names")
                or []
            ),
        )
        if not selected_profile:
            raise RuntimeError("Jellyseerr: unable to choose Radarr profile.")

        root_folders = test_data.get("rootFolders") or []
        preferred_root = radarr_cfg.get("root_folder") or radarr_app_cfg.get("root_folder")
        active_directory = self.choose_root_folder(root_folders, preferred_root)
        if not active_directory:
            raise RuntimeError("Jellyseerr: unable to choose Radarr root folder.")

        resolved_base_url = self.normalize_base_path(test_data.get("urlBase") or parsed["base_url"])
        payload = {
            "name": radarr_cfg.get("name", radarr_app_cfg.get("name", "Radarr")),
            "hostname": parsed["hostname"],
            "port": parsed["port"],
            "apiKey": radarr_api_key,
            "useSsl": parsed["use_ssl"],
            "baseUrl": resolved_base_url,
            "activeProfileId": self.to_int(selected_profile.get("id")),
            "activeProfileName": selected_profile.get("name"),
            "activeDirectory": active_directory,
            "is4k": self.bool_cfg(radarr_cfg, "is4k", False),
            "minimumAvailability": radarr_cfg.get("minimum_availability", "released"),
            "isDefault": self.bool_cfg(radarr_cfg, "is_default", True),
            "externalUrl": radarr_cfg.get("external_url", ""),
            "syncEnabled": self.bool_cfg(radarr_cfg, "sync_enabled", True),
            "preventSearch": self.bool_cfg(radarr_cfg, "prevent_search", False),
            "tagRequests": self.bool_cfg(radarr_cfg, "tag_requests", False),
            "tags": self.coerce_list(radarr_cfg.get("tags")),
            "overrideRule": self.coerce_list(radarr_cfg.get("override_rule")),
        }

        status, existing, body = self.http_request(
            jellyseerr_url, "/api/v1/settings/radarr", api_key=jellyseerr_key
        )
        if status != 200 or not isinstance(existing, list):
            raise RuntimeError(
                f"Jellyseerr: failed to list Radarr settings (HTTP {status}): {body}"
            )

        current = self.find_existing_servarr(
            existing,
            payload["name"],
            payload["hostname"],
            payload["port"],
            payload["baseUrl"],
            payload["is4k"],
        )
        if current:
            current_id = current.get("id")
            if current_id is None:
                self.log("[OK] Jellyseerr: existing Radarr mapping found (legacy entry without id)")
                return
            status, _, body = self.http_request(
                jellyseerr_url,
                f"/api/v1/settings/radarr/{current_id}",
                api_key=jellyseerr_key,
                method="PUT",
                payload=payload,
            )
            if status in (200, 201, 202):
                self.log("[OK] Jellyseerr: updated Radarr service mapping")
                return
            raise RuntimeError(
                f"Jellyseerr: failed updating Radarr mapping (HTTP {status}): {body}"
            )

        status, _, body = self.http_request(
            jellyseerr_url,
            "/api/v1/settings/radarr",
            api_key=jellyseerr_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            self.log("[OK] Jellyseerr: created Radarr service mapping")
            return
        raise RuntimeError(f"Jellyseerr: failed creating Radarr mapping (HTTP {status}): {body}")

    def ensure_sonarr(
        self,
        jellyseerr_url: str,
        jellyseerr_key: str,
        sonarr_app_cfg: dict[str, Any],
        sonarr_api_key: str,
        jelly_cfg: dict[str, Any],
    ) -> None:
        sonarr_cfg = jelly_cfg.get("sonarr") or {}
        if not self.bool_cfg(sonarr_cfg, "enabled", True):
            return

        parsed = self.parse_service_url(sonarr_app_cfg["url"], 8989)
        test_payload = {
            "hostname": parsed["hostname"],
            "port": parsed["port"],
            "apiKey": sonarr_api_key,
            "useSsl": parsed["use_ssl"],
            "baseUrl": parsed["base_url"],
        }
        status, test_data, body = self.http_request(
            jellyseerr_url,
            "/api/v1/settings/sonarr/test",
            api_key=jellyseerr_key,
            method="POST",
            payload=test_payload,
        )
        if status != 200 or not isinstance(test_data, dict):
            raise RuntimeError(f"Jellyseerr: Sonarr connection test failed (HTTP {status}): {body}")

        profiles = test_data.get("profiles") or []
        if not profiles:
            raise RuntimeError("Jellyseerr: Sonarr test returned no quality profiles.")
        selected_profile = self.choose_profile(
            profiles,
            preferred_id=sonarr_cfg.get("active_profile_id"),
            preferred_names=self.coerce_list(
                sonarr_cfg.get("quality_profile_preferred_names")
                or sonarr_cfg.get("preferred_profile_names")
                or []
            ),
        )
        if not selected_profile:
            raise RuntimeError("Jellyseerr: unable to choose Sonarr profile.")

        root_folders = test_data.get("rootFolders") or []
        preferred_root = sonarr_cfg.get("root_folder") or sonarr_app_cfg.get("root_folder")
        active_directory = self.choose_root_folder(root_folders, preferred_root)
        if not active_directory:
            raise RuntimeError("Jellyseerr: unable to choose Sonarr root folder.")

        language_profiles = test_data.get("languageProfiles") or []
        selected_language_profile = self.choose_profile(
            language_profiles, sonarr_cfg.get("active_language_profile_id")
        )
        active_language_profile_id = (
            self.to_int(selected_language_profile.get("id"))
            if selected_language_profile
            else self.to_int(sonarr_cfg.get("active_language_profile_id"))
        )

        active_anime_profile = self.choose_profile(
            profiles, sonarr_cfg.get("active_anime_profile_id")
        )
        active_anime_language_profile = self.choose_profile(
            language_profiles,
            sonarr_cfg.get("active_anime_language_profile_id"),
        )
        resolved_base_url = self.normalize_base_path(test_data.get("urlBase") or parsed["base_url"])

        series_type = str(sonarr_cfg.get("series_type", "standard")).strip().lower()
        if series_type not in ("standard", "daily", "anime"):
            series_type = "standard"
        anime_series_type = str(sonarr_cfg.get("anime_series_type", "anime")).strip().lower()
        if anime_series_type not in ("standard", "daily", "anime"):
            anime_series_type = "anime"
        monitor_new_items = str(sonarr_cfg.get("monitor_new_items", "all")).strip().lower()
        if monitor_new_items not in ("all", "none"):
            monitor_new_items = "all"

        payload = {
            "name": sonarr_cfg.get("name", sonarr_app_cfg.get("name", "Sonarr")),
            "hostname": parsed["hostname"],
            "port": parsed["port"],
            "apiKey": sonarr_api_key,
            "useSsl": parsed["use_ssl"],
            "baseUrl": resolved_base_url,
            "activeProfileId": self.to_int(selected_profile.get("id")),
            "activeProfileName": selected_profile.get("name"),
            "activeLanguageProfileId": active_language_profile_id,
            "activeDirectory": active_directory,
            "seriesType": series_type,
            "animeSeriesType": anime_series_type,
            "activeAnimeProfileId": (
                self.to_int(active_anime_profile.get("id"))
                if active_anime_profile
                else self.to_int(sonarr_cfg.get("active_anime_profile_id"))
            ),
            "activeAnimeProfileName": (
                active_anime_profile.get("name") if active_anime_profile else None
            ),
            "activeAnimeLanguageProfileId": (
                self.to_int(active_anime_language_profile.get("id"))
                if active_anime_language_profile
                else self.to_int(sonarr_cfg.get("active_anime_language_profile_id"))
            ),
            "activeAnimeDirectory": sonarr_cfg.get("active_anime_directory"),
            "is4k": self.bool_cfg(sonarr_cfg, "is4k", False),
            "isDefault": self.bool_cfg(sonarr_cfg, "is_default", True),
            "enableSeasonFolders": self.bool_cfg(sonarr_cfg, "enable_season_folders", True),
            "externalUrl": sonarr_cfg.get("external_url", ""),
            "syncEnabled": self.bool_cfg(sonarr_cfg, "sync_enabled", True),
            "preventSearch": self.bool_cfg(sonarr_cfg, "prevent_search", False),
            "tagRequests": self.bool_cfg(sonarr_cfg, "tag_requests", False),
            "monitorNewItems": monitor_new_items,
            "tags": self.coerce_list(sonarr_cfg.get("tags")),
            "animeTags": self.coerce_list(sonarr_cfg.get("anime_tags")),
            "overrideRule": self.coerce_list(sonarr_cfg.get("override_rule")),
        }

        status, existing, body = self.http_request(
            jellyseerr_url, "/api/v1/settings/sonarr", api_key=jellyseerr_key
        )
        if status != 200 or not isinstance(existing, list):
            raise RuntimeError(
                f"Jellyseerr: failed to list Sonarr settings (HTTP {status}): {body}"
            )

        current = self.find_existing_servarr(
            existing,
            payload["name"],
            payload["hostname"],
            payload["port"],
            payload["baseUrl"],
            payload["is4k"],
        )
        if current:
            current_id = current.get("id")
            if current_id is None:
                self.log("[OK] Jellyseerr: existing Sonarr mapping found (legacy entry without id)")
                return
            status, _, body = self.http_request(
                jellyseerr_url,
                f"/api/v1/settings/sonarr/{current_id}",
                api_key=jellyseerr_key,
                method="PUT",
                payload=payload,
            )
            if status in (200, 201, 202):
                self.log("[OK] Jellyseerr: updated Sonarr service mapping")
                return
            raise RuntimeError(
                f"Jellyseerr: failed updating Sonarr mapping (HTTP {status}): {body}"
            )

        status, _, body = self.http_request(
            jellyseerr_url,
            "/api/v1/settings/sonarr",
            api_key=jellyseerr_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            self.log("[OK] Jellyseerr: created Sonarr service mapping")
            return
        raise RuntimeError(f"Jellyseerr: failed creating Sonarr mapping (HTTP {status}): {body}")

    def configure_via_settings_file(
        self,
        cfg: dict[str, Any],
        arr_apps: list[dict[str, Any]],
        app_keys: dict[str, str],
        config_root: str,
    ) -> None:
        jelly_cfg = cfg.get("jellyseerr") or {}
        settings_path = Path(config_root) / "jellyseerr" / "settings.json"
        settings = self.read_json_file(settings_path)

        main_cfg = settings.setdefault("main", {})
        if self.bool_cfg(jelly_cfg, "set_media_server_type_jellyfin", True):
            main_cfg["mediaServerType"] = 2
        settings.setdefault("public", {})["initialized"] = True

        jellyfin_cfg = jelly_cfg.get("jellyfin") or {}
        if self.bool_cfg(jellyfin_cfg, "configure", False):
            jellyfin_api_key = self.resolve_jellyfin_api_key(jellyfin_cfg, config_root)
            if not jellyfin_api_key:
                raise RuntimeError(
                    "Jellyseerr file bootstrap: jellyfin.configure=true but Jellyfin API key could not be resolved."
                )
            parsed_jf = self.parse_service_url(
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
            self.log("[OK] Jellyseerr: wrote Jellyfin settings via file bootstrap")

        radarr_app = self.get_arr_app(arr_apps, "Radarr")
        if (
            radarr_app
            and "Radarr" in app_keys
            and self.bool_cfg((jelly_cfg.get("radarr") or {}), "enabled", True)
        ):
            radarr_cfg = jelly_cfg.get("radarr") or {}
            radarr_url = self.normalize_url(radarr_app["url"])
            radarr_api_base = self.detect_arr_api_base("Radarr", radarr_url, app_keys["Radarr"])
            radarr_profile_names = self.coerce_list(
                radarr_cfg.get("quality_profile_preferred_names")
                or radarr_app.get("quality_profile_preferred_names")
                or []
            )
            radarr_profile = self.get_arr_quality_profile(
                "Radarr",
                radarr_url,
                radarr_api_base,
                app_keys["Radarr"],
                preferred_id=radarr_cfg.get("active_profile_id"),
                preferred_names=radarr_profile_names,
            )
            radarr_root = self.get_arr_root_folder_path(
                "Radarr",
                radarr_url,
                radarr_api_base,
                app_keys["Radarr"],
                radarr_app.get("root_folder"),
            )
            parsed_radarr = self.parse_service_url(radarr_app["url"], 7878)
            settings["radarr"] = [
                {
                    "name": radarr_cfg.get("name", "Radarr"),
                    "hostname": parsed_radarr["hostname"],
                    "port": parsed_radarr["port"],
                    "apiKey": app_keys["Radarr"],
                    "useSsl": parsed_radarr["use_ssl"],
                    "baseUrl": parsed_radarr["base_url"],
                    "activeProfileId": self.to_int(radarr_profile.get("id"), 1),
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
            self.log("[OK] Jellyseerr: wrote Radarr settings via file bootstrap")

        sonarr_app = self.get_arr_app(arr_apps, "Sonarr")
        if (
            sonarr_app
            and "Sonarr" in app_keys
            and self.bool_cfg((jelly_cfg.get("sonarr") or {}), "enabled", True)
        ):
            sonarr_cfg = jelly_cfg.get("sonarr") or {}
            sonarr_url = self.normalize_url(sonarr_app["url"])
            sonarr_api_base = self.detect_arr_api_base("Sonarr", sonarr_url, app_keys["Sonarr"])
            sonarr_profile_names = self.coerce_list(
                sonarr_cfg.get("quality_profile_preferred_names")
                or sonarr_app.get("quality_profile_preferred_names")
                or []
            )
            sonarr_profile = self.get_arr_quality_profile(
                "Sonarr",
                sonarr_url,
                sonarr_api_base,
                app_keys["Sonarr"],
                preferred_id=sonarr_cfg.get("active_profile_id"),
                preferred_names=sonarr_profile_names,
            )
            sonarr_root = self.get_arr_root_folder_path(
                "Sonarr",
                sonarr_url,
                sonarr_api_base,
                app_keys["Sonarr"],
                sonarr_app.get("root_folder"),
            )
            parsed_sonarr = self.parse_service_url(sonarr_app["url"], 8989)
            settings["sonarr"] = [
                {
                    "name": sonarr_cfg.get("name", "Sonarr"),
                    "hostname": parsed_sonarr["hostname"],
                    "port": parsed_sonarr["port"],
                    "apiKey": app_keys["Sonarr"],
                    "useSsl": parsed_sonarr["use_ssl"],
                    "baseUrl": parsed_sonarr["base_url"],
                    "activeProfileId": self.to_int(sonarr_profile.get("id"), 1),
                    "activeProfileName": str(sonarr_profile.get("name") or "Default"),
                    "activeDirectory": sonarr_root,
                    "activeLanguageProfileId": self.get_sonarr_language_profile_id(
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
            self.log("[OK] Jellyseerr: wrote Sonarr settings via file bootstrap")

        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.log("[OK] Jellyseerr: settings file bootstrap applied")

    @staticmethod
    def permission_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "(http 403)" in text
            or "permission to access this endpoint" in text
            or "you do not have permission" in text
        )

    def configure(
        self,
        cfg: dict[str, Any],
        arr_apps: list[dict[str, Any]],
        app_keys: dict[str, str],
        config_root: str,
        wait_timeout: int,
    ) -> None:
        jelly_cfg = cfg.get("jellyseerr") or {}
        if not self.bool_cfg(jelly_cfg, "enabled", False):
            return

        jellyseerr_url = self.normalize_url(jelly_cfg.get("url", "http://jellyseerr:5055"))
        self.wait_for_service("Jellyseerr", jellyseerr_url, "/api/v1/status", wait_timeout)

        jellyseerr_key = self.read_jellyseerr_api_key(config_root, wait_timeout)
        radarr_app = self.get_arr_app(arr_apps, "Radarr")
        sonarr_app = self.get_arr_app(arr_apps, "Sonarr")
        enforced_file_bootstrap = False

        try:
            self.ensure_main_settings(jellyseerr_url, jellyseerr_key, jelly_cfg)
            self.ensure_jellyfin_settings(jellyseerr_url, jellyseerr_key, jelly_cfg, config_root)

            if radarr_app and "Radarr" in app_keys:
                self.ensure_radarr(
                    jellyseerr_url, jellyseerr_key, radarr_app, app_keys["Radarr"], jelly_cfg
                )
            else:
                self.log("[WARN] Jellyseerr: Radarr app config not found; skipping Radarr mapping.")

            if sonarr_app and "Sonarr" in app_keys:
                self.ensure_sonarr(
                    jellyseerr_url, jellyseerr_key, sonarr_app, app_keys["Sonarr"], jelly_cfg
                )
            else:
                self.log("[WARN] Jellyseerr: Sonarr app config not found; skipping Sonarr mapping.")
        except Exception as exc:
            if not self.permission_error(exc):
                raise
            self.log(
                "[WARN] Jellyseerr API bootstrap hit permission gate; "
                "applying settings-file bootstrap fallback."
            )
            self.configure_via_settings_file(cfg, arr_apps, app_keys, config_root)
            enforced_file_bootstrap = True

        if self.bool_cfg(jelly_cfg, "enforce_settings_file", True) and not enforced_file_bootstrap:
            self.configure_via_settings_file(cfg, arr_apps, app_keys, config_root)
