"""Jellyfin bootstrap service logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .config_models import JellyfinLiveTvConfig

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ToIntFn = Callable[[Any, Any], Any]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ResolveApiKeyFn = Callable[[dict[str, Any], str], str]
JellyfinRequestFn = Callable[..., tuple[int, Any, str]]
PrepareTunerFn = Callable[[dict[str, Any], list[dict[str, Any]], str, dict[str, set[str]]], str]
PrepareGuideFn = Callable[[dict[str, Any], list[dict[str, Any]], str], str]
LoadStateFn = Callable[[str, dict[str, Any]], dict[str, Any]]
ResolveTunerTypeFn = Callable[[str, str, str], str]
NormalizeEnabledTunersFn = Callable[[Any, dict[str, Any]], list[str]]
DeleteEntityFn = Callable[[str, str, str, str], None]
TriggerRefreshFn = Callable[[str, str, str, str], tuple[bool, str]]


@dataclass
class JellyfinLiveTvDependencies:
    log: LogFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    to_int: ToIntFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    resolve_api_key: ResolveApiKeyFn
    jellyfin_request: JellyfinRequestFn
    prepare_tuner_url: PrepareTunerFn
    prepare_guide_path: PrepareGuideFn
    load_state: LoadStateFn
    resolve_tuner_type_id: ResolveTunerTypeFn
    normalize_enabled_tuner_ids: NormalizeEnabledTunersFn
    delete_entity: DeleteEntityFn
    trigger_refresh: TriggerRefreshFn


@dataclass
class JellyfinService:
    deps: JellyfinLiveTvDependencies

    def ensure_livetv(self, cfg: dict[str, Any], config_root: str, wait_timeout: int) -> None:
        d = self.deps
        live_cfg = cfg.get("jellyfin_livetv") or {}
        live_model = JellyfinLiveTvConfig.from_dict(live_cfg)
        if not live_model.enabled:
            return

        tuners = d.coerce_list(live_cfg.get("tuners"))
        guides = d.coerce_list(live_cfg.get("guides"))
        refresh_on_bootstrap = bool(live_model.refresh_on_bootstrap)
        cleanup_duplicates = bool(live_model.cleanup_duplicates)
        recreate_managed_guides = bool(live_model.recreate_managed_guides)
        prune_unmanaged_tuners = bool(live_model.prune_unmanaged_tuners)
        prune_unmanaged_guides = bool(live_model.prune_unmanaged_guides)
        fallback_enable_all_tuners = bool(
            live_model.fallback_enable_all_tuners_when_mapping_missing
        )
        if not tuners and not guides and not refresh_on_bootstrap:
            d.log("[WARN] Jellyfin Live TV: enabled but no tuners/guides configured.")
            return

        prepared_tuners = []
        guide_channel_ids_cache: dict[str, set[str]] = {}
        for tuner in tuners:
            if not isinstance(tuner, dict):
                raise RuntimeError(
                    f"Jellyfin Live TV: each tuner entry must be an object, got: {tuner}"
                )
            source_url = str(tuner.get("url") or "").strip()
            if not source_url:
                raise RuntimeError("Jellyfin Live TV: tuner entry missing required field 'url'")

            effective_url = d.prepare_tuner_url(
                tuner,
                guides,
                config_root,
                guide_channel_ids_cache=guide_channel_ids_cache,
            )
            prepared = dict(tuner)
            prepared["_effective_url"] = effective_url
            prepared_tuners.append(prepared)

        prepared_guides = []
        for guide in guides:
            if not isinstance(guide, dict):
                raise RuntimeError(
                    f"Jellyfin Live TV: each guide entry must be an object, got: {guide}"
                )
            guide_path = str(guide.get("path") or "").strip()
            if not guide_path:
                raise RuntimeError("Jellyfin Live TV: guide entry missing required field 'path'")
            effective_path = d.prepare_guide_path(guide, prepared_tuners, config_root)
            prepared = dict(guide)
            prepared["_effective_path"] = effective_path
            prepared_guides.append(prepared)

        desired_tuner_keys = set()
        for tuner in prepared_tuners:
            desired_tuner_keys.add(
                (
                    str(tuner.get("type", "m3u")).strip().lower(),
                    str(tuner.get("_effective_url") or "").strip(),
                )
            )
        desired_guide_keys = set()
        for guide in prepared_guides:
            if not isinstance(guide, dict):
                continue
            guide_path = str(guide.get("_effective_path") or guide.get("path") or "").strip()
            if not guide_path:
                continue
            guide_type = str(guide.get("type", "xmltv")).strip().lower()
            desired_guide_keys.add((guide_type, guide_path))

        jellyfin_url = d.normalize_url(live_cfg.get("url", "http://jellyfin:8096"))
        d.wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

        jellyfin_api_key = d.resolve_api_key(live_cfg, config_root)
        if not jellyfin_api_key:
            raise RuntimeError(
                "Jellyfin Live TV: API key unavailable. Set JELLYFIN_API_KEY or keep "
                "jellyfin_livetv.auto_discover_api_key_from_db=true and ensure "
                "jellyfin/data/jellyfin.db contains a usable key."
            )

        status, _, body = d.jellyfin_request(jellyfin_url, "/LiveTv/Info", jellyfin_api_key)
        if status != 200:
            raise RuntimeError(
                f"Jellyfin Live TV: failed auth/health check against /LiveTv/Info "
                f"(HTTP {status}): {body}"
            )

        added_tuners = 0
        added_guides = 0
        state = {
            "tuner_keys": set(),
            "guide_keys": set(),
            "tuner_ids_by_key": {},
        }

        if tuners or guides:
            state = d.load_state(config_root, live_cfg)
            total_existing_tuners = sum(
                len(items) for items in (state.get("tuners_by_key") or {}).values()
            )
            total_existing_guides = sum(
                len(items) for items in (state.get("guides_by_key") or {}).values()
            )
            d.log(
                "[INFO] Jellyfin Live TV: state before reconcile "
                f"(tuner_keys={len(state.get('tuner_keys') or [])}, "
                f"tuners={total_existing_tuners}, "
                f"guide_keys={len(state.get('guide_keys') or [])}, "
                f"guides={total_existing_guides}, "
                f"source={state.get('source_path', 'unknown')})"
            )
            cleanup_changed = False
            recreate_changed = False

            if cleanup_duplicates:
                for tuner_key, tuner_entries in (state.get("tuners_by_key") or {}).items():
                    if len(tuner_entries) <= 1:
                        continue
                    for duplicate in tuner_entries[1:]:
                        tuner_id = str((duplicate or {}).get("id") or "").strip()
                        if not tuner_id:
                            continue
                        d.delete_entity(jellyfin_url, jellyfin_api_key, "tuner", tuner_id)
                        cleanup_changed = True
                        d.log(
                            "[INFO] Jellyfin Live TV: removed duplicate tuner "
                            f"(type={tuner_key[0]}, url={tuner_key[1]}, id={tuner_id})"
                        )

                for guide_key, guide_entries in (state.get("guides_by_key") or {}).items():
                    if len(guide_entries) <= 1:
                        continue
                    for duplicate in guide_entries[1:]:
                        guide_id = str((duplicate or {}).get("id") or "").strip()
                        if not guide_id:
                            continue
                        d.delete_entity(jellyfin_url, jellyfin_api_key, "guide", guide_id)
                        cleanup_changed = True
                        d.log(
                            "[INFO] Jellyfin Live TV: removed duplicate guide "
                            f"(type={guide_key[0]}, path={guide_key[1]}, id={guide_id})"
                        )

            if cleanup_changed:
                state = d.load_state(config_root, live_cfg)
                total_existing_tuners = sum(
                    len(items) for items in (state.get("tuners_by_key") or {}).values()
                )
                total_existing_guides = sum(
                    len(items) for items in (state.get("guides_by_key") or {}).values()
                )
                d.log(
                    "[INFO] Jellyfin Live TV: state after cleanup "
                    f"(tuner_keys={len(state.get('tuner_keys') or [])}, "
                    f"tuners={total_existing_tuners}, "
                    f"guide_keys={len(state.get('guide_keys') or [])}, "
                    f"guides={total_existing_guides}, "
                    f"source={state.get('source_path', 'unknown')})"
                )

            if recreate_managed_guides and prepared_guides:
                for guide in prepared_guides:
                    if not isinstance(guide, dict):
                        continue
                    guide_path = str(
                        guide.get("_effective_path") or guide.get("path") or ""
                    ).strip()
                    if not guide_path:
                        continue
                    guide_type = str(guide.get("type", "xmltv")).strip().lower()
                    guide_key = (guide_type, guide_path)
                    for existing_guide in (state.get("guides_by_key") or {}).get(guide_key, []):
                        guide_id = str((existing_guide or {}).get("id") or "").strip()
                        if not guide_id:
                            continue
                        d.delete_entity(jellyfin_url, jellyfin_api_key, "guide", guide_id)
                        recreate_changed = True
                        d.log(
                            "[INFO] Jellyfin Live TV: recreated managed guide binding "
                            f"(type={guide_type}, path={guide_path}, id={guide_id})"
                        )

            if recreate_changed:
                state = d.load_state(config_root, live_cfg)
                total_existing_tuners = sum(
                    len(items) for items in (state.get("tuners_by_key") or {}).values()
                )
                total_existing_guides = sum(
                    len(items) for items in (state.get("guides_by_key") or {}).values()
                )
                d.log(
                    "[INFO] Jellyfin Live TV: state after cleanup "
                    f"(tuner_keys={len(state.get('tuner_keys') or [])}, "
                    f"tuners={total_existing_tuners}, "
                    f"guide_keys={len(state.get('guide_keys') or [])}, "
                    f"guides={total_existing_guides}, "
                    f"source={state.get('source_path', 'unknown')})"
                )

            prune_changed = False
            if prune_unmanaged_tuners:
                for tuner_key, tuner_entries in (state.get("tuners_by_key") or {}).items():
                    if tuner_key in desired_tuner_keys:
                        continue
                    for entry in tuner_entries:
                        tuner_id = str((entry or {}).get("id") or "").strip()
                        if not tuner_id:
                            continue
                        d.delete_entity(jellyfin_url, jellyfin_api_key, "tuner", tuner_id)
                        prune_changed = True
                        d.log(
                            "[INFO] Jellyfin Live TV: pruned unmanaged tuner "
                            f"(type={tuner_key[0]}, url={tuner_key[1]}, id={tuner_id})"
                        )

            if prune_unmanaged_guides:
                for guide_key, guide_entries in (state.get("guides_by_key") or {}).items():
                    if guide_key in desired_guide_keys:
                        continue
                    for entry in guide_entries:
                        guide_id = str((entry or {}).get("id") or "").strip()
                        if not guide_id:
                            continue
                        d.delete_entity(jellyfin_url, jellyfin_api_key, "guide", guide_id)
                        prune_changed = True
                        d.log(
                            "[INFO] Jellyfin Live TV: pruned unmanaged guide "
                            f"(type={guide_key[0]}, path={guide_key[1]}, id={guide_id})"
                        )

            if prune_changed:
                state = d.load_state(config_root, live_cfg)
                total_existing_tuners = sum(
                    len(items) for items in (state.get("tuners_by_key") or {}).values()
                )
                total_existing_guides = sum(
                    len(items) for items in (state.get("guides_by_key") or {}).values()
                )
                d.log(
                    "[INFO] Jellyfin Live TV: state after pruning unmanaged entries "
                    f"(tuner_keys={len(state.get('tuner_keys') or [])}, "
                    f"tuners={total_existing_tuners}, "
                    f"guide_keys={len(state.get('guide_keys') or [])}, "
                    f"guides={total_existing_guides}, "
                    f"source={state.get('source_path', 'unknown')})"
                )

            for tuner in prepared_tuners:
                tuner_url = str(tuner.get("_effective_url") or tuner.get("url") or "").strip()
                tuner_type_requested = str(tuner.get("type", "m3u")).strip()
                tuner_type = d.resolve_tuner_type_id(
                    jellyfin_url, jellyfin_api_key, tuner_type_requested
                )
                key = (tuner_type.lower(), tuner_url)
                if key in state["tuner_keys"]:
                    d.log(f"[OK] Jellyfin Live TV: tuner already exists ({tuner_type} {tuner_url})")
                    continue

                payload = {
                    "Type": tuner_type,
                    "Url": tuner_url,
                    "FriendlyName": str(
                        tuner.get("friendly_name")
                        or tuner.get("name")
                        or f"{tuner_type.upper()} {tuner_url}"
                    ),
                    "ImportFavoritesOnly": bool(tuner.get("import_favorites_only", False)),
                    "AllowHWTranscoding": bool(tuner.get("allow_hw_transcoding", True)),
                    "AllowFmp4TranscodingContainer": bool(
                        tuner.get("allow_fmp4_transcoding_container", False)
                    ),
                    "AllowStreamSharing": bool(tuner.get("allow_stream_sharing", True)),
                    "EnableStreamLooping": bool(tuner.get("enable_stream_looping", False)),
                    "IgnoreDts": bool(tuner.get("ignore_dts", True)),
                    "ReadAtNativeFramerate": bool(tuner.get("read_at_native_framerate", False)),
                }
                max_bitrate = d.to_int(tuner.get("fallback_max_streaming_bitrate"), 30000000)
                if max_bitrate is not None:
                    payload["FallbackMaxStreamingBitrate"] = max_bitrate

                status, data, body = d.jellyfin_request(
                    jellyfin_url,
                    "/LiveTv/TunerHosts",
                    jellyfin_api_key,
                    method="POST",
                    payload=payload,
                )
                if status not in (200, 201, 202):
                    raise RuntimeError(
                        f"Jellyfin Live TV: failed creating tuner {tuner_url} (HTTP {status}): {body}"
                    )

                created_id = (
                    str((data or {}).get("Id") or "").strip() if isinstance(data, dict) else ""
                )
                state["tuner_keys"].add(key)
                if created_id:
                    state["tuner_ids_by_key"][key] = created_id
                added_tuners += 1
                d.log(f"[OK] Jellyfin Live TV: added tuner ({tuner_type} {tuner_url})")

            state = d.load_state(config_root, live_cfg)

            for guide in prepared_guides:
                if not isinstance(guide, dict):
                    raise RuntimeError(
                        f"Jellyfin Live TV: each guide entry must be an object, got: {guide}"
                    )

                guide_path = str(guide.get("_effective_path") or guide.get("path") or "").strip()
                if not guide_path:
                    raise RuntimeError(
                        "Jellyfin Live TV: guide entry missing required field 'path'"
                    )

                guide_type = str(guide.get("type", "xmltv")).strip()
                guide_key = (guide_type.lower(), guide_path)
                if guide_key in state["guide_keys"]:
                    d.log(
                        f"[OK] Jellyfin Live TV: guide already exists ({guide_type} {guide_path})"
                    )
                    continue

                payload = {
                    "Type": guide_type,
                    "Path": guide_path,
                    "EnableAllTuners": bool(guide.get("enable_all_tuners", True)),
                }

                enabled_tuners = d.normalize_enabled_tuner_ids(guide.get("enabled_tuners"), state)
                if enabled_tuners:
                    payload["EnabledTuners"] = enabled_tuners
                    payload["EnableAllTuners"] = False
                elif not payload["EnableAllTuners"] and fallback_enable_all_tuners:
                    payload["EnableAllTuners"] = True
                    d.log(
                        "[WARN] Jellyfin Live TV: guide enabled_tuners resolved empty; "
                        f"falling back to EnableAllTuners=true for path={guide_path}"
                    )

                optional_string_fields = {
                    "username": "Username",
                    "password": "Password",
                    "listings_id": "ListingsId",
                    "zip_code": "ZipCode",
                    "country": "Country",
                    "preferred_language": "PreferredLanguage",
                    "user_agent": "UserAgent",
                }
                for src_key, dst_key in optional_string_fields.items():
                    value = guide.get(src_key)
                    if value is not None and str(value).strip():
                        payload[dst_key] = str(value).strip()

                optional_array_fields = {
                    "news_categories": "NewsCategories",
                    "sports_categories": "SportsCategories",
                    "kids_categories": "KidsCategories",
                    "movie_categories": "MovieCategories",
                    "channel_mappings": "ChannelMappings",
                }
                for src_key, dst_key in optional_array_fields.items():
                    if src_key in guide:
                        payload[dst_key] = d.coerce_list(guide.get(src_key))

                status, _, body = d.jellyfin_request(
                    jellyfin_url,
                    "/LiveTv/ListingProviders",
                    jellyfin_api_key,
                    method="POST",
                    payload=payload,
                )
                if status not in (200, 201, 202):
                    raise RuntimeError(
                        f"Jellyfin Live TV: failed creating guide {guide_path} (HTTP {status}): {body}"
                    )

                state["guide_keys"].add(guide_key)
                added_guides += 1
                d.log(f"[OK] Jellyfin Live TV: added guide ({guide_type} {guide_path})")

        if added_tuners == 0 and added_guides == 0 and refresh_on_bootstrap:
            d.log(
                "[INFO] Jellyfin Live TV: no tuner/guide changes, requesting refresh for UX consistency."
            )

        if added_tuners > 0 or added_guides > 0 or refresh_on_bootstrap:
            refresh_ops = [
                ("/LiveTv/RefreshChannels", "channel refresh"),
                ("/LiveTv/RefreshGuide", "guide refresh"),
            ]
            for path, label in refresh_ops:
                ok, detail = d.trigger_refresh(jellyfin_url, jellyfin_api_key, path, label)
                if ok:
                    d.log(f"[OK] Jellyfin Live TV: {detail}")
                else:
                    d.log(f"[WARN] Jellyfin Live TV: {detail}")

        d.log(
            "[OK] Jellyfin Live TV: reconcile complete "
            f"(tuners_added={added_tuners}, guides_added={added_guides})"
        )
