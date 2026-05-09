"""Jellyfin library reconcile service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib import parse
from media_stack.core.service_registry.registry import service_internal_url

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
ResolveApiKeyFn = Callable[[dict[str, Any], str], str]
JellyfinRequestFn = Callable[..., tuple[int, Any, str]]
BuildQueryPathFn = Callable[[str, dict[str, Any]], str]
ReorderProviderNamesFn = Callable[[list[str], list[str]], list[str]]
ApplyArtworkProfileFn = Callable[[list[Any], list[str], dict[str, Any]], list[dict[str, Any]]]


@dataclass
class JellyfinLibrariesDependencies:
    log: LogFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    resolve_api_key: ResolveApiKeyFn
    jellyfin_request: JellyfinRequestFn
    build_query_path: BuildQueryPathFn
    reorder_provider_names: ReorderProviderNamesFn
    apply_artwork_profile: ApplyArtworkProfileFn


@dataclass
class JellyfinLibrariesService:
    deps: JellyfinLibrariesDependencies

    @staticmethod
    def _normalize_names(entries: list[Any]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in entries:
            text = str(raw or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    def _names_from_option_info(self, entries: list[Any]) -> list[str]:
        out: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or item.get("name") or "").strip()
            if name:
                out.append(name)
        return self._normalize_names(out)

    @staticmethod
    def _default_image_options(entries: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            image_type = str(item.get("Type") or "").strip()
            if not image_type:
                continue
            out.append(
                {
                    "Type": image_type,
                    "Limit": int(item.get("Limit", 0) or 0),
                    "MinWidth": int(item.get("MinWidth", 0) or 0),
                }
            )
        return out

    def _normalize_type_options(self, entries: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            type_name = str(item.get("Type") or item.get("type") or "").strip()
            if not type_name:
                continue
            out.append(
                {
                    "Type": type_name,
                    "MetadataFetchers": self._normalize_names(
                        item.get("MetadataFetchers") or item.get("metadataFetchers") or []
                    ),
                    "MetadataFetcherOrder": self._normalize_names(
                        item.get("MetadataFetcherOrder") or item.get("metadataFetcherOrder") or []
                    ),
                    "ImageFetchers": self._normalize_names(
                        item.get("ImageFetchers") or item.get("imageFetchers") or []
                    ),
                    "ImageFetcherOrder": self._normalize_names(
                        item.get("ImageFetcherOrder") or item.get("imageFetcherOrder") or []
                    ),
                    "ImageOptions": self._default_image_options(
                        item.get("ImageOptions") or item.get("imageOptions") or []
                    ),
                }
            )
        return out

    def _type_options_from_available_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        type_entries: list[dict[str, Any]] = []
        for raw in self.deps.coerce_list(payload.get("TypeOptions")):
            if not isinstance(raw, dict):
                continue
            type_name = str(raw.get("Type") or "").strip()
            if not type_name:
                continue
            metadata_fetchers = self._names_from_option_info(raw.get("MetadataFetchers") or [])
            image_fetchers = self._names_from_option_info(raw.get("ImageFetchers") or [])
            type_entries.append(
                {
                    "Type": type_name,
                    "MetadataFetchers": metadata_fetchers,
                    "MetadataFetcherOrder": list(metadata_fetchers),
                    "ImageFetchers": image_fetchers,
                    "ImageFetcherOrder": list(image_fetchers),
                    "ImageOptions": self._default_image_options(
                        raw.get("DefaultImageOptions") or []
                    ),
                    "_supported_image_types": self._normalize_names(
                        raw.get("SupportedImageTypes") or []
                    ),
                }
            )
        return type_entries

    def _reconcile_type_options(
        self,
        current_options: list[Any],
        available_payload: dict[str, Any],
        metadata_priority: list[Any],
        image_priority: list[Any],
        artwork_profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        current = self._normalize_type_options(current_options)
        available = self._type_options_from_available_payload(available_payload)
        available_by_type = {
            str(entry.get("Type") or "").strip().lower(): entry for entry in available
        }
        current_by_type = {str(entry.get("Type") or "").strip().lower(): entry for entry in current}

        ordered_keys: list[str] = []
        for entry in available:
            key = str(entry.get("Type") or "").strip().lower()
            if key and key not in ordered_keys:
                ordered_keys.append(key)
        for entry in current:
            key = str(entry.get("Type") or "").strip().lower()
            if key and key not in ordered_keys:
                ordered_keys.append(key)

        merged: list[dict[str, Any]] = []
        for key in ordered_keys:
            base = available_by_type.get(key, {})
            cur = current_by_type.get(key, {})
            type_name = str(cur.get("Type") or base.get("Type") or "").strip()
            if not type_name:
                continue

            metadata_fetchers = self._normalize_names(
                self.deps.coerce_list(cur.get("MetadataFetchers"))
                + self.deps.coerce_list(base.get("MetadataFetchers"))
            )
            image_fetchers = self._normalize_names(
                self.deps.coerce_list(cur.get("ImageFetchers"))
                + self.deps.coerce_list(base.get("ImageFetchers"))
            )

            metadata_order_seed = self._normalize_names(
                self.deps.coerce_list(cur.get("MetadataFetcherOrder"))
                + self.deps.coerce_list(base.get("MetadataFetcherOrder"))
                + metadata_fetchers
            )
            image_order_seed = self._normalize_names(
                self.deps.coerce_list(cur.get("ImageFetcherOrder"))
                + self.deps.coerce_list(base.get("ImageFetcherOrder"))
                + image_fetchers
            )

            metadata_order = self.deps.reorder_provider_names(
                metadata_order_seed,
                self._normalize_names(metadata_priority),
            )
            image_order = self.deps.reorder_provider_names(
                image_order_seed,
                self._normalize_names(image_priority),
            )

            metadata_set = {name.lower() for name in metadata_fetchers}
            image_set = {name.lower() for name in image_fetchers}
            metadata_order = [name for name in metadata_order if name.lower() in metadata_set]
            image_order = [name for name in image_order if name.lower() in image_set]

            image_options = self.deps.apply_artwork_profile(
                self.deps.coerce_list(cur.get("ImageOptions"))
                or self.deps.coerce_list(base.get("ImageOptions")),
                self.deps.coerce_list(base.get("_supported_image_types")),
                artwork_profile,
            )

            merged.append(
                {
                    "Type": type_name,
                    "MetadataFetchers": metadata_fetchers,
                    "MetadataFetcherOrder": metadata_order,
                    "ImageFetchers": image_fetchers,
                    "ImageFetcherOrder": image_order,
                    "ImageOptions": image_options,
                }
            )

        return merged

    def ensure(self, cfg: dict[str, Any], config_root: str, wait_timeout: int) -> None:
        d = self.deps
        libraries_cfg = cfg.get("jellyfin_libraries") or {}
        if not d.bool_cfg(libraries_cfg, "enabled", False):
            return

        jellyfin_url = d.normalize_url(libraries_cfg.get("url", service_internal_url("jellyfin")))
        d.wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

        jellyfin_api_key = d.resolve_api_key(libraries_cfg, config_root)
        if not jellyfin_api_key:
            raise RuntimeError(
                "Jellyfin libraries: API key unavailable. Set JELLYFIN_API_KEY or keep "
                "jellyfin_libraries.auto_discover_api_key_from_db=true."
            )

        libraries = d.coerce_list(libraries_cfg.get("libraries"))
        if not libraries:
            d.log("[WARN] Jellyfin libraries: enabled but no libraries were declared.")
            return

        status, existing, body = d.jellyfin_request(
            jellyfin_url,
            "/Library/VirtualFolders",
            jellyfin_api_key,
        )
        if status != 200 or not isinstance(existing, list):
            raise RuntimeError(
                f"Jellyfin libraries: failed listing virtual folders (HTTP {status}): {body}"
            )

        existing_by_name: dict[str, dict[str, Any]] = {}
        for folder in existing:
            if not isinstance(folder, dict):
                continue
            name = str(folder.get("Name") or folder.get("name") or "").strip().lower()
            if name:
                existing_by_name[name] = folder

        tune_cfg = libraries_cfg.get("tuning")
        if not isinstance(tune_cfg, dict):
            tune_cfg = {}

        tune_enabled = d.bool_cfg(tune_cfg, "enabled", True)
        realtime_monitor = d.bool_cfg(tune_cfg, "enable_realtime_monitor", True)
        enable_trickplay_movies = d.bool_cfg(tune_cfg, "enable_preview_thumbnails_movies", True)
        enable_trickplay_tv = d.bool_cfg(tune_cfg, "enable_preview_thumbnails_tv", True)
        preferred_metadata_language = str(
            tune_cfg.get("preferred_metadata_language", "en") or ""
        ).strip()
        metadata_country_code = str(tune_cfg.get("metadata_country_code", "US") or "").strip()
        metadata_priority = d.coerce_list(
            tune_cfg.get(
                "metadata_provider_priority",
                ["TheMovieDb", "Fanart", "The Open Movie Database"],
            )
        )
        image_priority = d.coerce_list(
            tune_cfg.get(
                "image_provider_priority",
                [
                    "TheMovieDb",
                    "Fanart",
                    "The Open Movie Database",
                    "Embedded Image Extractor",
                    "Screen Grabber",
                ],
            )
        )
        artwork_profile = tune_cfg.get("artwork_profile")
        if not isinstance(artwork_profile, dict):
            artwork_profile = {
                "Backdrop": {"limit": 3, "min_width": 1280},
                "Logo": {"limit": 1, "min_width": 0},
                "Primary": {"limit": 1, "min_width": 0},
                "Thumb": {"limit": 1, "min_width": 0},
            }

        available_options_cache: dict[str, dict[str, Any]] = {}

        def library_available_options(collection_type: str) -> dict[str, Any]:
            key = str(collection_type or "").strip().lower()
            if not key:
                return {}
            if key in available_options_cache:
                return available_options_cache[key]
            path = d.build_query_path(
                "/Libraries/AvailableOptions",
                {"libraryContentType": key, "isNewLibrary": "false"},
            )
            status, payload, body = d.jellyfin_request(jellyfin_url, path, jellyfin_api_key)
            if status == 200 and isinstance(payload, dict):
                available_options_cache[key] = payload
                return payload
            d.log(
                f"[WARN] Jellyfin libraries: could not fetch available options for {key} "
                f"(HTTP {status}): {body}"
            )
            available_options_cache[key] = {}
            return {}

        added = 0
        tuned = 0
        scan_requested = False

        for entry in libraries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            collection_type = str(entry.get("collection_type") or "").strip()
            paths = [str(p).strip() for p in d.coerce_list(entry.get("paths")) if str(p).strip()]

            if not name or not paths:
                continue

            key = name.lower()
            current = existing_by_name.get(key)
            if current:
                current_paths = {str(p).rstrip("/") for p in (current.get("Locations") or [])}
                desired_paths = {p.rstrip("/") for p in paths}
                if desired_paths.issubset(current_paths):
                    d.log(f"[OK] Jellyfin libraries: already present: {name}")
                else:
                    d.log(
                        f"[WARN] Jellyfin libraries: '{name}' exists but paths differ "
                        f"(existing={sorted(current_paths)}, desired={sorted(desired_paths)}). "
                        "Update manually in Jellyfin if you want path changes."
                    )

            if not current:
                query = {
                    "name": name,
                    "collectionType": collection_type,
                    "paths": paths[0],
                    "refreshLibrary": "true",
                }
                path = f"/Library/VirtualFolders?{parse.urlencode(query)}"
                status, _, body = d.jellyfin_request(
                    jellyfin_url,
                    path,
                    jellyfin_api_key,
                    method="POST",
                )
                if status in (200, 201, 202, 204):
                    added += 1
                    scan_requested = True
                    d.log(f"[OK] Jellyfin libraries: created library '{name}' -> {paths[0]}")
                    status, existing, body = d.jellyfin_request(
                        jellyfin_url,
                        "/Library/VirtualFolders",
                        jellyfin_api_key,
                    )
                    if status != 200 or not isinstance(existing, list):
                        raise RuntimeError(
                            "Jellyfin libraries: created library but failed reloading folders "
                            f"(HTTP {status}): {body}"
                        )
                    existing_by_name = {}
                    for folder in existing:
                        if not isinstance(folder, dict):
                            continue
                        folder_name = (
                            str(folder.get("Name") or folder.get("name") or "").strip().lower()
                        )
                        if folder_name:
                            existing_by_name[folder_name] = folder
                    current = existing_by_name.get(key)
                else:
                    raise RuntimeError(
                        f"Jellyfin libraries: failed creating '{name}' (HTTP {status}): {body}"
                    )

            if not tune_enabled or not isinstance(current, dict):
                continue

            item_id = str(current.get("ItemId") or current.get("itemId") or "").strip()
            if not item_id:
                d.log(f"[WARN] Jellyfin libraries: cannot tune '{name}' (missing ItemId)")
                continue

            current_options = current.get("LibraryOptions")
            if not isinstance(current_options, dict):
                d.log(f"[WARN] Jellyfin libraries: cannot tune '{name}' (missing LibraryOptions)")
                continue

            desired_options = json.loads(json.dumps(current_options))
            if realtime_monitor and "EnableRealtimeMonitor" in desired_options:
                desired_options["EnableRealtimeMonitor"] = True
            if preferred_metadata_language and "PreferredMetadataLanguage" in desired_options:
                desired_options["PreferredMetadataLanguage"] = preferred_metadata_language
            if metadata_country_code and "MetadataCountryCode" in desired_options:
                desired_options["MetadataCountryCode"] = metadata_country_code

            collection_key = (
                str(collection_type or current.get("CollectionType") or "").strip().lower()
            )
            if collection_key in ("movies", "tvshows"):
                enable_trickplay = (
                    enable_trickplay_movies if collection_key == "movies" else enable_trickplay_tv
                )
                if enable_trickplay:
                    for trickplay_key in (
                        "EnableTrickplayImageExtraction",
                        "ExtractTrickplayImagesDuringLibraryScan",
                    ):
                        if trickplay_key in desired_options:
                            desired_options[trickplay_key] = True

            available_payload = library_available_options(collection_key)
            desired_options["TypeOptions"] = self._reconcile_type_options(
                desired_options.get("TypeOptions") or [],
                available_payload,
                metadata_priority,
                image_priority,
                artwork_profile,
            )

            if desired_options != current_options:
                update_payload = {"Id": item_id, "LibraryOptions": desired_options}
                status, _, body = d.jellyfin_request(
                    jellyfin_url,
                    "/Library/VirtualFolders/LibraryOptions",
                    jellyfin_api_key,
                    method="POST",
                    payload=update_payload,
                )
                if status in (200, 201, 202, 204):
                    tuned += 1
                    scan_requested = True
                    d.log(
                        f"[OK] Jellyfin libraries: tuned '{name}' options "
                        f"(realtime={desired_options.get('EnableRealtimeMonitor')}, "
                        f"trickplay={desired_options.get('EnableTrickplayImageExtraction')})"
                    )
                else:
                    raise RuntimeError(
                        f"Jellyfin libraries: failed updating options for '{name}' "
                        f"(HTTP {status}): {body}"
                    )
            else:
                d.log(
                    f"[OK] Jellyfin libraries: tuning already matches desired config for '{name}'"
                )

        if scan_requested and d.bool_cfg(tune_cfg, "scan_all_libraries_after_reconcile", True):
            status, _, body = d.jellyfin_request(
                jellyfin_url,
                "/Library/Refresh",
                jellyfin_api_key,
                method="POST",
            )
            if status in (200, 201, 202, 204):
                d.log("[OK] Jellyfin libraries: triggered library refresh")
            else:
                d.log(
                    f"[WARN] Jellyfin libraries: failed to trigger library refresh "
                    f"(HTTP {status}): {body}"
                )

        d.log(f"[OK] Jellyfin libraries: reconcile complete (added={added}, tuned={tuned})")
