"""Operational helpers for Jellyseerr bootstrap service."""

from __future__ import annotations

from typing import Any


class JellyseerrApiOps:

    def ensure_main_settings(self, 
        svc,
        jellyseerr_url: str,
        jellyseerr_key: str,
        jelly_cfg: dict[str, Any],
    ) -> None:
        media_server_type = jelly_cfg.get("media_server_type")
        if media_server_type is None and svc.bool_cfg(
            jelly_cfg, "set_media_server_type_jellyfin", True
        ):
            media_server_type = 2

        if media_server_type is None:
            return

        status, current, body = svc.http_request(
            jellyseerr_url, "/api/v1/settings/main", api_key=jellyseerr_key
        )
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError(f"Jellyseerr: failed to read main settings (HTTP {status}): {body}")

        desired_type = int(media_server_type)
        local_login = svc.bool_cfg(jelly_cfg, "enable_local_login", True)
        media_server_login = svc.bool_cfg(jelly_cfg, "enable_media_server_login", False)

        updates: dict[str, Any] = {}
        if svc.to_int(current.get("mediaServerType")) != desired_type:
            updates["mediaServerType"] = desired_type
        if current.get("localLogin") != local_login:
            updates["localLogin"] = local_login
        if current.get("mediaServerLogin") != media_server_login:
            updates["mediaServerLogin"] = media_server_login
        if media_server_login is False and current.get("newPlexLogin") is not False:
            updates["newPlexLogin"] = False

        if not updates:
            svc.log(f"[OK] Jellyseerr: main settings already correct")
        else:
            status, _, body = svc.http_request(
                jellyseerr_url,
                "/api/v1/settings/main",
                api_key=jellyseerr_key,
                method="POST",
                payload=updates,
            )
            if status not in (200, 201, 202):
                raise RuntimeError(f"Jellyseerr: failed to set main settings (HTTP {status}): {body}")
            svc.log(f"[OK] Jellyseerr: main settings updated ({', '.join(updates.keys())})")

        # Mark Jellyseerr as initialized so the setup wizard is skipped.
        _ensure_initialized(svc, jellyseerr_url, jellyseerr_key)

    @staticmethod
    def _ensure_initialized(
        svc,
        jellyseerr_url: str,
        jellyseerr_key: str,
    ) -> None:
        """Set public.initialized=true via the Jellyseerr API."""
        status, current, body = svc.http_request(
            jellyseerr_url, "/api/v1/settings/public", api_key=jellyseerr_key
        )
        if status == 200 and isinstance(current, dict) and current.get("initialized"):
            return
        status, _, body = svc.http_request(
            jellyseerr_url,
            "/api/v1/settings/initialize",
            api_key=jellyseerr_key,
            method="POST",
            payload={},
        )
        if status in (200, 201, 202, 204):
            svc.log("[OK] Jellyseerr: marked as initialized")
        else:
            # Fallback: try /api/v1/settings/public POST
            status2, _, body2 = svc.http_request(
                jellyseerr_url,
                "/api/v1/settings/public",
                api_key=jellyseerr_key,
                method="POST",
                payload={"initialized": True},
            )
            if status2 in (200, 201, 202, 204):
                svc.log("[OK] Jellyseerr: marked as initialized (via public settings)")
            else:
                svc.log(f"[WARN] Jellyseerr: could not mark as initialized "
                         f"(init: HTTP {status}, public: HTTP {status2})")

    def ensure_jellyfin_settings(self, 
        svc,
        jellyseerr_url: str,
        jellyseerr_key: str,
        jelly_cfg: dict[str, Any],
        config_root: str,
    ) -> None:
        jellyfin_cfg = jelly_cfg.get("jellyfin") or {}
        if not svc.bool_cfg(jellyfin_cfg, "configure", False):
            return

        jellyfin_api_key = svc.resolve_jellyfin_api_key(jellyfin_cfg, config_root)
        if not jellyfin_api_key:
            raise RuntimeError(
                "Jellyseerr: jellyfin.configure=true but Jellyfin API key could not be resolved."
            )

        jellyfin_url = jellyfin_cfg.get("url", "http://jellyfin:8096")
        parsed = svc.parse_service_url(jellyfin_url, 8096)
        payload = {
            "ip": parsed["hostname"],
            "port": parsed["port"],
            "useSsl": parsed["use_ssl"],
            "urlBase": parsed["base_url"],
            "apiKey": jellyfin_api_key,
            "externalHostname": jellyfin_cfg.get("external_url", ""),
            "jellyfinForgotPasswordUrl": jellyfin_cfg.get("forgot_password_url", ""),
        }

        status, _, body = svc.http_request(
            jellyseerr_url,
            "/api/v1/settings/jellyfin",
            api_key=jellyseerr_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            svc.log("[OK] Jellyseerr: configured Jellyfin connection")
            return

        raise RuntimeError(f"Jellyseerr: failed to configure Jellyfin settings (HTTP {status}): {body}")

    def ensure_radarr(self, 
        svc,
        jellyseerr_url: str,
        jellyseerr_key: str,
        radarr_app_cfg: dict[str, Any],
        radarr_api_key: str,
        jelly_cfg: dict[str, Any],
    ) -> None:
        radarr_cfg = jelly_cfg.get("radarr") or {}
        if not svc.bool_cfg(radarr_cfg, "enabled", True):
            return

        parsed = svc.parse_service_url(radarr_app_cfg["url"], 7878)
        test_payload = {
            "hostname": parsed["hostname"],
            "port": parsed["port"],
            "apiKey": radarr_api_key,
            "useSsl": parsed["use_ssl"],
            "baseUrl": parsed["base_url"],
        }
        status, test_data, body = svc.http_request(
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
        selected_profile = svc.choose_profile(
            profiles,
            preferred_id=radarr_cfg.get("active_profile_id"),
            preferred_names=svc.coerce_list(
                radarr_cfg.get("quality_profile_preferred_names")
                or radarr_cfg.get("preferred_profile_names")
                or []
            ),
        )
        if not selected_profile:
            raise RuntimeError("Jellyseerr: unable to choose Radarr profile.")

        root_folders = test_data.get("rootFolders") or []
        preferred_root = radarr_cfg.get("root_folder") or radarr_app_cfg.get("root_folder")
        active_directory = svc.choose_root_folder(root_folders, preferred_root)
        if not active_directory:
            raise RuntimeError("Jellyseerr: unable to choose Radarr root folder.")

        resolved_base_url = svc.normalize_base_path(test_data.get("urlBase") or parsed["base_url"])
        payload = {
            "name": radarr_cfg.get("name", radarr_app_cfg.get("name", "Radarr")),
            "hostname": parsed["hostname"],
            "port": parsed["port"],
            "apiKey": radarr_api_key,
            "useSsl": parsed["use_ssl"],
            "baseUrl": resolved_base_url,
            "activeProfileId": svc.to_int(selected_profile.get("id")),
            "activeProfileName": selected_profile.get("name"),
            "activeDirectory": active_directory,
            "is4k": svc.bool_cfg(radarr_cfg, "is4k", False),
            "minimumAvailability": radarr_cfg.get("minimum_availability", "released"),
            "isDefault": svc.bool_cfg(radarr_cfg, "is_default", True),
            "externalUrl": radarr_cfg.get("external_url", ""),
            "syncEnabled": svc.bool_cfg(radarr_cfg, "sync_enabled", True),
            "preventSearch": svc.bool_cfg(radarr_cfg, "prevent_search", False),
            "tagRequests": svc.bool_cfg(radarr_cfg, "tag_requests", False),
            "tags": svc.coerce_list(radarr_cfg.get("tags")),
            "overrideRule": svc.coerce_list(radarr_cfg.get("override_rule")),
        }

        status, existing, body = svc.http_request(
            jellyseerr_url, "/api/v1/settings/radarr", api_key=jellyseerr_key
        )
        if status != 200 or not isinstance(existing, list):
            raise RuntimeError(f"Jellyseerr: failed to list Radarr settings (HTTP {status}): {body}")

        current = svc.find_existing_servarr(
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
                svc.log("[OK] Jellyseerr: existing Radarr mapping found (legacy entry without id)")
                return
            status, _, body = svc.http_request(
                jellyseerr_url,
                f"/api/v1/settings/radarr/{current_id}",
                api_key=jellyseerr_key,
                method="PUT",
                payload=payload,
            )
            if status in (200, 201, 202):
                svc.log("[OK] Jellyseerr: updated Radarr service mapping")
                return
            raise RuntimeError(f"Jellyseerr: failed updating Radarr mapping (HTTP {status}): {body}")

        status, _, body = svc.http_request(
            jellyseerr_url,
            "/api/v1/settings/radarr",
            api_key=jellyseerr_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            svc.log("[OK] Jellyseerr: created Radarr service mapping")
            return
        raise RuntimeError(f"Jellyseerr: failed creating Radarr mapping (HTTP {status}): {body}")

    def ensure_sonarr(self, 
        svc,
        jellyseerr_url: str,
        jellyseerr_key: str,
        sonarr_app_cfg: dict[str, Any],
        sonarr_api_key: str,
        jelly_cfg: dict[str, Any],
    ) -> None:
        sonarr_cfg = jelly_cfg.get("sonarr") or {}
        if not svc.bool_cfg(sonarr_cfg, "enabled", True):
            return

        parsed = svc.parse_service_url(sonarr_app_cfg["url"], 8989)
        test_payload = {
            "hostname": parsed["hostname"],
            "port": parsed["port"],
            "apiKey": sonarr_api_key,
            "useSsl": parsed["use_ssl"],
            "baseUrl": parsed["base_url"],
        }
        status, test_data, body = svc.http_request(
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
        selected_profile = svc.choose_profile(
            profiles,
            preferred_id=sonarr_cfg.get("active_profile_id"),
            preferred_names=svc.coerce_list(
                sonarr_cfg.get("quality_profile_preferred_names")
                or sonarr_cfg.get("preferred_profile_names")
                or []
            ),
        )
        if not selected_profile:
            raise RuntimeError("Jellyseerr: unable to choose Sonarr profile.")

        root_folders = test_data.get("rootFolders") or []
        preferred_root = sonarr_cfg.get("root_folder") or sonarr_app_cfg.get("root_folder")
        active_directory = svc.choose_root_folder(root_folders, preferred_root)
        if not active_directory:
            raise RuntimeError("Jellyseerr: unable to choose Sonarr root folder.")

        language_profiles = test_data.get("languageProfiles") or []
        selected_language_profile = svc.choose_profile(
            language_profiles, sonarr_cfg.get("active_language_profile_id")
        )
        active_language_profile_id = (
            svc.to_int(selected_language_profile.get("id"), 1)
            if selected_language_profile
            else svc.to_int(sonarr_cfg.get("active_language_profile_id"), 1)
        )

        active_anime_profile = svc.choose_profile(profiles, sonarr_cfg.get("active_anime_profile_id"))
        active_anime_language_profile = svc.choose_profile(
            language_profiles,
            sonarr_cfg.get("active_anime_language_profile_id"),
        )
        resolved_base_url = svc.normalize_base_path(test_data.get("urlBase") or parsed["base_url"])

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
            "activeProfileId": svc.to_int(selected_profile.get("id")),
            "activeProfileName": selected_profile.get("name"),
            "activeLanguageProfileId": active_language_profile_id,
            "activeDirectory": active_directory,
            "seriesType": series_type,
            "animeSeriesType": anime_series_type,
            "activeAnimeProfileId": (
                svc.to_int(active_anime_profile.get("id"))
                if active_anime_profile
                else svc.to_int(sonarr_cfg.get("active_anime_profile_id"))
            ),
            "activeAnimeProfileName": (
                active_anime_profile.get("name") if active_anime_profile else None
            ),
            "activeAnimeLanguageProfileId": (
                svc.to_int(active_anime_language_profile.get("id"), 1)
                if active_anime_language_profile
                else svc.to_int(sonarr_cfg.get("active_anime_language_profile_id"), 1)
            ),
            "activeAnimeDirectory": sonarr_cfg.get("active_anime_directory"),
            "is4k": svc.bool_cfg(sonarr_cfg, "is4k", False),
            "isDefault": svc.bool_cfg(sonarr_cfg, "is_default", True),
            "enableSeasonFolders": svc.bool_cfg(sonarr_cfg, "enable_season_folders", True),
            "externalUrl": sonarr_cfg.get("external_url", ""),
            "syncEnabled": svc.bool_cfg(sonarr_cfg, "sync_enabled", True),
            "preventSearch": svc.bool_cfg(sonarr_cfg, "prevent_search", False),
            "tagRequests": svc.bool_cfg(sonarr_cfg, "tag_requests", False),
            "monitorNewItems": monitor_new_items,
            "tags": svc.coerce_list(sonarr_cfg.get("tags")),
            "animeTags": svc.coerce_list(sonarr_cfg.get("anime_tags")),
            "overrideRule": svc.coerce_list(sonarr_cfg.get("override_rule")),
        }

        status, existing, body = svc.http_request(
            jellyseerr_url, "/api/v1/settings/sonarr", api_key=jellyseerr_key
        )
        if status != 200 or not isinstance(existing, list):
            raise RuntimeError(f"Jellyseerr: failed to list Sonarr settings (HTTP {status}): {body}")

        current = svc.find_existing_servarr(
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
                svc.log("[OK] Jellyseerr: existing Sonarr mapping found (legacy entry without id)")
                return
            status, _, body = svc.http_request(
                jellyseerr_url,
                f"/api/v1/settings/sonarr/{current_id}",
                api_key=jellyseerr_key,
                method="PUT",
                payload=payload,
            )
            if status in (200, 201, 202):
                svc.log("[OK] Jellyseerr: updated Sonarr service mapping")
                return
            raise RuntimeError(f"Jellyseerr: failed updating Sonarr mapping (HTTP {status}): {body}")

        status, _, body = svc.http_request(
            jellyseerr_url,
            "/api/v1/settings/sonarr",
            api_key=jellyseerr_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            svc.log("[OK] Jellyseerr: created Sonarr service mapping")
            return
        raise RuntimeError(f"Jellyseerr: failed creating Sonarr mapping (HTTP {status}): {body}")


_instance = JellyseerrApiOps()
ensure_main_settings = _instance.ensure_main_settings
ensure_jellyfin_settings = _instance.ensure_jellyfin_settings
ensure_radarr = _instance.ensure_radarr
ensure_sonarr = _instance.ensure_sonarr
