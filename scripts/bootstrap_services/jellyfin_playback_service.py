"""Jellyfin playback defaults bootstrap service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib import parse

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ResolveApiKeyFn = Callable[[dict[str, Any], str], str]
JellyfinRequestFn = Callable[..., tuple[int, Any, str]]
BuildQueryPathFn = Callable[[str, dict[str, Any]], str]
ResolveUserIdFn = Callable[[dict[str, Any], str, str], str]
NormalizePluginNameFn = Callable[[str], str]


@dataclass
class JellyfinPlaybackDependencies:
    log: LogFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    resolve_api_key: ResolveApiKeyFn
    jellyfin_request: JellyfinRequestFn
    build_query_path: BuildQueryPathFn
    resolve_user_id: ResolveUserIdFn
    normalize_plugin_name: NormalizePluginNameFn


@dataclass
class JellyfinPlaybackService:
    deps: JellyfinPlaybackDependencies

    def ensure(self, cfg: dict[str, Any], config_root: str, wait_timeout: int) -> None:
        d = self.deps
        playback_cfg = cfg.get("jellyfin_playback") or {}
        if not d.bool_cfg(playback_cfg, "enabled", False):
            return

        jellyfin_url = d.normalize_url(playback_cfg.get("url", "http://jellyfin:8096"))
        d.wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

        jellyfin_api_key = d.resolve_api_key(playback_cfg, config_root)
        if not jellyfin_api_key:
            raise RuntimeError(
                "Jellyfin playback: API key unavailable. Set JELLYFIN_API_KEY or keep "
                "jellyfin_playback.auto_discover_api_key_from_db=true."
            )

        user_id = d.resolve_user_id(playback_cfg, jellyfin_url, jellyfin_api_key)
        if not user_id:
            raise RuntimeError(
                "Jellyfin playback: no Jellyfin user id could be resolved. Set JELLYFIN_USER_ID or "
                "keep jellyfin_playback.auto_discover_user_id=true."
            )

        user_defaults = playback_cfg.get("user_defaults")
        if not isinstance(user_defaults, dict) or not user_defaults:
            user_defaults = {
                "AudioLanguagePreference": "eng",
                "PlayDefaultAudioTrack": True,
                "SubtitleLanguagePreference": "eng",
                "SubtitleMode": "Smart",
                "RememberAudioSelections": True,
                "RememberSubtitleSelections": True,
                "EnableNextEpisodeAutoPlay": True,
                "DisplayCollectionsView": False,
                "HidePlayedInLatest": False,
            }

        user_path = d.build_query_path(
            f"/Users/{parse.quote(user_id, safe='')}",
            {},
        )
        status, user_payload, body = d.jellyfin_request(jellyfin_url, user_path, jellyfin_api_key)
        if status != 200 or not isinstance(user_payload, dict):
            raise RuntimeError(
                f"Jellyfin playback: failed reading user ({user_id}) (HTTP {status}): {body}"
            )

        current_user_cfg = user_payload.get("Configuration")
        if not isinstance(current_user_cfg, dict):
            raise RuntimeError("Jellyfin playback: user payload missing Configuration object.")

        desired_user_cfg = dict(current_user_cfg)
        changed_user_keys = []
        for key, value in user_defaults.items():
            if desired_user_cfg.get(key) != value:
                desired_user_cfg[key] = value
                changed_user_keys.append(key)

        # Hide selected system views from "My Media" to keep home UX focused.
        home_media_cfg = playback_cfg.get("home_media")
        if not isinstance(home_media_cfg, dict):
            home_media_cfg = {}
        if d.bool_cfg(home_media_cfg, "enabled", True):
            status, views_payload, body = d.jellyfin_request(
                jellyfin_url,
                f"/Users/{parse.quote(user_id, safe='')}/Views",
                jellyfin_api_key,
            )
            if status != 200 or not isinstance(views_payload, dict):
                d.log(
                    "[WARN] Jellyfin playback: could not read user views for My Media exclusions "
                    f"(HTTP {status}): {body}"
                )
            else:
                items = views_payload.get("Items")
                views = items if isinstance(items, list) else []
                managed_view_ids: set[str] = set()
                selected_view_ids: set[str] = set()
                additional_types = {
                    str(item).strip().lower()
                    for item in d.coerce_list(home_media_cfg.get("additional_excluded_collection_types"))
                    if str(item).strip()
                }
                exclude_collections = d.bool_cfg(home_media_cfg, "exclude_collections", True)
                exclude_playlists = d.bool_cfg(home_media_cfg, "exclude_playlists", True)
                cleanup_managed = d.bool_cfg(home_media_cfg, "cleanup_managed_exclusions", True)

                for view in views:
                    if not isinstance(view, dict):
                        continue
                    view_id = str(view.get("Id") or view.get("id") or "").strip()
                    collection_type = str(
                        view.get("CollectionType") or view.get("collectionType") or ""
                    ).strip().lower()
                    if not view_id or not collection_type:
                        continue

                    managed_type = collection_type in (
                        "boxsets",
                        "collections",
                        "playlists",
                    ) or collection_type in additional_types
                    if managed_type:
                        managed_view_ids.add(view_id)

                    if collection_type in ("boxsets", "collections") and exclude_collections:
                        selected_view_ids.add(view_id)
                    elif collection_type == "playlists" and exclude_playlists:
                        selected_view_ids.add(view_id)
                    elif collection_type in additional_types:
                        selected_view_ids.add(view_id)

                existing_excludes = {
                    str(item).strip()
                    for item in d.coerce_list(desired_user_cfg.get("MyMediaExcludes"))
                    if str(item).strip()
                }
                desired_excludes = set(existing_excludes)
                if cleanup_managed:
                    desired_excludes -= managed_view_ids
                desired_excludes |= selected_view_ids

                if desired_excludes != existing_excludes:
                    desired_user_cfg["MyMediaExcludes"] = sorted(desired_excludes)
                    changed_user_keys.append("MyMediaExcludes")

        if changed_user_keys:
            update_path = d.build_query_path("/Users/Configuration", {"userId": user_id})
            status, _, body = d.jellyfin_request(
                jellyfin_url,
                update_path,
                jellyfin_api_key,
                method="POST",
                payload=desired_user_cfg,
            )
            if status not in (200, 201, 202, 204):
                raise RuntimeError(
                    f"Jellyfin playback: failed updating user defaults (HTTP {status}): {body}"
                )
            d.log(
                "[OK] Jellyfin playback: updated user defaults "
                f"(keys={','.join(changed_user_keys)})"
            )
        else:
            d.log("[OK] Jellyfin playback: user defaults already match desired config")

        server_defaults = playback_cfg.get("server_defaults")
        if not isinstance(server_defaults, dict) or not server_defaults:
            server_defaults = {
                "PreferredMetadataLanguage": "en",
                "MetadataCountryCode": "US",
                "UICulture": "en-US",
                "ImageSavingConvention": "Compatible",
                "ChapterImageResolution": "P720",
                "EnableGroupingMoviesIntoCollections": True,
                "EnableGroupingShowsIntoCollections": True,
                "EnableExternalContentInSuggestions": True,
            }

        status, server_payload, body = d.jellyfin_request(
            jellyfin_url,
            "/System/Configuration",
            jellyfin_api_key,
        )
        if status != 200 or not isinstance(server_payload, dict):
            raise RuntimeError(
                f"Jellyfin playback: failed reading server config (HTTP {status}): {body}"
            )

        desired_server_cfg = dict(server_payload)
        changed_server_keys = []
        for key, value in server_defaults.items():
            if key not in desired_server_cfg:
                continue
            if desired_server_cfg.get(key) != value:
                desired_server_cfg[key] = value
                changed_server_keys.append(key)

        if changed_server_keys:
            status, _, body = d.jellyfin_request(
                jellyfin_url,
                "/System/Configuration",
                jellyfin_api_key,
                method="POST",
                payload=desired_server_cfg,
            )
            if status not in (200, 201, 202, 204):
                raise RuntimeError(
                    f"Jellyfin playback: failed updating server defaults (HTTP {status}): {body}"
                )
            d.log(
                "[OK] Jellyfin playback: updated server defaults "
                f"(keys={','.join(changed_server_keys)})"
            )
        else:
            d.log("[OK] Jellyfin playback: server defaults already match desired config")

        display_cfg = playback_cfg.get("display_preferences")
        if not isinstance(display_cfg, dict):
            display_cfg = {}
        if d.bool_cfg(display_cfg, "enabled", True):
            client = str(display_cfg.get("client") or "emby").strip() or "emby"
            preference_ids = [
                str(item).strip()
                for item in d.coerce_list(
                    display_cfg.get(
                        "preference_ids",
                        ["usersettings", "home", "movies", "tv"],
                    )
                )
                if str(item).strip()
            ]
            show_backdrop = d.bool_cfg(display_cfg, "show_backdrop", True)
            custom_prefs_cfg = display_cfg.get("custom_prefs")
            if not isinstance(custom_prefs_cfg, dict):
                custom_prefs_cfg = {
                    "enableNextVideoInfoOverlay": True,
                    "enableBackdrops": True,
                    "enableThemeVideos": True,
                }
            update_existing_only = d.bool_cfg(display_cfg, "update_existing_custom_prefs_only", False)

            updated_display = 0
            for pref_id in preference_ids:
                path = d.build_query_path(
                    f"/DisplayPreferences/{parse.quote(pref_id, safe='')}",
                    {"userId": user_id, "client": client},
                )
                status, display_payload, body = d.jellyfin_request(jellyfin_url, path, jellyfin_api_key)
                if status != 200 or not isinstance(display_payload, dict):
                    d.log(
                        f"[WARN] Jellyfin playback: unable to load DisplayPreferences '{pref_id}' "
                        f"(HTTP {status}): {body}"
                    )
                    continue

                desired_display = dict(display_payload)
                changed = False
                if desired_display.get("ShowBackdrop") != show_backdrop:
                    desired_display["ShowBackdrop"] = show_backdrop
                    changed = True

                custom_prefs = desired_display.get("CustomPrefs")
                if not isinstance(custom_prefs, dict):
                    custom_prefs = {}
                new_custom = dict(custom_prefs)
                custom_changed = False
                for key, value in custom_prefs_cfg.items():
                    pref_key = str(key or "").strip()
                    if not pref_key:
                        continue
                    if update_existing_only and pref_key not in custom_prefs:
                        continue
                    if isinstance(value, bool):
                        pref_value = "True" if value else "False"
                    else:
                        pref_value = str(value)
                    if new_custom.get(pref_key) != pref_value:
                        new_custom[pref_key] = pref_value
                        custom_changed = True
                if custom_changed:
                    desired_display["CustomPrefs"] = new_custom
                    changed = True

                if not changed:
                    continue

                status, _, body = d.jellyfin_request(
                    jellyfin_url,
                    path,
                    jellyfin_api_key,
                    method="POST",
                    payload=desired_display,
                )
                if status not in (200, 201, 202, 204):
                    d.log(
                        f"[WARN] Jellyfin playback: failed updating DisplayPreferences '{pref_id}' "
                        f"(HTTP {status}): {body}"
                    )
                    continue
                updated_display += 1

            if updated_display:
                d.log(
                    "[OK] Jellyfin playback: updated display preferences "
                    f"(count={updated_display}, client={client})"
                )
            else:
                d.log("[OK] Jellyfin playback: display preferences already match desired config")

        if d.bool_cfg(playback_cfg, "check_intro_skip_plugin", True):
            status, installed_plugins, body = d.jellyfin_request(
                jellyfin_url,
                "/Plugins",
                jellyfin_api_key,
            )
            if status == 200 and isinstance(installed_plugins, list):
                has_intro_skip = any(
                    d.normalize_plugin_name(item.get("Name") or item.get("name") or "")
                    == d.normalize_plugin_name("Intro Skipper")
                    for item in installed_plugins
                    if isinstance(item, dict)
                )
                if has_intro_skip:
                    d.log("[OK] Jellyfin playback: Intro Skipper plugin is installed")
                else:
                    d.log(
                        "[WARN] Jellyfin playback: Intro Skipper plugin is not installed; "
                        "enable jellyfin_plugins.install for Intro Skipper."
                    )
            else:
                d.log(
                    f"[WARN] Jellyfin playback: could not verify Intro Skipper install "
                    f"(HTTP {status}): {body}"
                )

        d.log("[OK] Jellyfin playback: reconcile complete")
