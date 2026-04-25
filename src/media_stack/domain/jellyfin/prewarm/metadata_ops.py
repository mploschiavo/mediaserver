"""Metadata/artwork prewarm helpers for Jellyfin."""

from __future__ import annotations

from typing import Any

from .sidecar_ops import normalize_text_list


class JellyfinMetadataOps:

    def item_has_artwork(self, item: dict[str, Any]) -> bool:
        image_tags = item.get("ImageTags")
        if isinstance(image_tags, dict):
            if any(str(value or "").strip() for value in image_tags.values()):
                return True
        if str(item.get("PrimaryImageTag") or "").strip():
            return True
        if str(item.get("AlbumPrimaryImageTag") or "").strip():
            return True
        if str(item.get("PrimaryImageItemId") or "").strip():
            return True
        backdrop_tags = item.get("BackdropImageTags")
        return bool(isinstance(backdrop_tags, list) and backdrop_tags)

    def item_has_overview(self, item: dict[str, Any]) -> bool:
        return bool(str(item.get("Overview") or "").strip())

    def run_metadata_backfill(self,
        service,
        prewarm_cfg: dict[str, Any],
        jellyfin_url: str,
        jellyfin_api_key: str,
    ) -> None:
        d = service.deps
        backfill_cfg = prewarm_cfg.get("metadata_backfill")
        if not isinstance(backfill_cfg, dict):
            backfill_cfg = {}
        if not d.bool_cfg(backfill_cfg, "enabled", True):
            return

        opts = self._resolve_backfill_options(d, backfill_cfg)
        libraries_payload = self._fetch_virtual_folders(
            d, jellyfin_url, jellyfin_api_key, opts["required"],
        )
        if libraries_payload is None:
            return

        totals = {
            "candidates": 0, "requested": 0, "failed": 0,
            "folders_attempted": 0, "folders_refreshed": 0, "folders_failed": 0,
        }
        selected_libraries: list[tuple[str, str]] = []

        for library in libraries_payload:
            if not isinstance(library, dict):
                continue
            lib_ctx = self._library_context(library, opts["libraries_filter"])
            if lib_ctx is None:
                continue
            selected_libraries.append((lib_ctx["display_name"], lib_ctx["id"]))
            self._refresh_library_items(
                d=d, jellyfin_url=jellyfin_url, jellyfin_api_key=jellyfin_api_key,
                opts=opts, lib=lib_ctx, totals=totals,
            )

        if opts["refresh_collection_folders"]:
            self._refresh_collection_folders(
                d=d, jellyfin_url=jellyfin_url, jellyfin_api_key=jellyfin_api_key,
                refresh_params=opts["refresh_params"],
                selected_libraries=selected_libraries, totals=totals,
            )

        self._finalize_backfill_run(d, totals, opts["required"])

    @staticmethod
    def _finalize_backfill_run(d, totals: dict[str, int], required: bool) -> None:
        """Emit the summary log and raise if ``required`` asked for clean success."""
        if (totals["failed"] or totals["folders_failed"]) and required:
            raise RuntimeError(
                "Jellyfin prewarm: metadata backfill had refresh failures "
                f"(requested={totals['requested']}, failed={totals['failed']}, "
                f"folder_failed={totals['folders_failed']})"
            )
        d.log(
            "[OK] Jellyfin prewarm: metadata backfill complete "
            f"(candidates={totals['candidates']}, requested={totals['requested']}, "
            f"failed={totals['failed']}, folder_refreshed={totals['folders_refreshed']}, "
            f"folder_failed={totals['folders_failed']})"
        )

    @staticmethod
    def _resolve_backfill_options(d, backfill_cfg: dict[str, Any]) -> dict[str, Any]:
        """Resolve every tunable from the backfill config with safe fallbacks.

        Isolates the numeric coercions and default-query shape so the
        caller can treat the result as fully-typed options.
        """
        libraries_filter = {
            token.lower()
            for token in normalize_text_list(
                backfill_cfg.get("libraries"), ["Movies", "TV Shows", "Music", "Books"]
            )
        }
        try:
            max_refresh_per_library = int(backfill_cfg.get("max_refresh_per_library") or 80)
        except Exception:
            max_refresh_per_library = 80
        try:
            sample_multiplier = int(backfill_cfg.get("sample_multiplier") or 4)
        except Exception:
            sample_multiplier = 4
        sample_limit = max(1, max_refresh_per_library * max(1, sample_multiplier))
        refresh_params = backfill_cfg.get("refresh_query")
        if not isinstance(refresh_params, dict):
            refresh_params = {
                "metadataRefreshMode": "FullRefresh",
                "imageRefreshMode": "FullRefresh",
                "replaceAllMetadata": "true",
                "replaceAllImages": "true",
            }
        return {
            "libraries_filter": libraries_filter,
            "refresh_missing_primary": d.bool_cfg(backfill_cfg, "refresh_missing_primary_image", True),
            "refresh_missing_overview": d.bool_cfg(backfill_cfg, "refresh_missing_overview", True),
            "refresh_collection_folders": d.bool_cfg(backfill_cfg, "refresh_collection_folder_images", True),
            "required": d.bool_cfg(backfill_cfg, "required", False),
            "max_refresh_per_library": max_refresh_per_library,
            "sample_limit": sample_limit,
            "refresh_params": refresh_params,
        }

    @staticmethod
    def _fetch_virtual_folders(
        d, jellyfin_url: str, jellyfin_api_key: str, required: bool,
    ) -> list | None:
        """Return the VirtualFolders list, or None after logging on failure.

        Honors ``required``: raises if the caller demanded success,
        otherwise logs a warning and returns None so the caller can
        bail out without failing the whole prewarm.
        """
        status, payload, body = d.jellyfin_request(
            jellyfin_url, "/Library/VirtualFolders", jellyfin_api_key
        )
        if status == 200 and isinstance(payload, list):
            return payload
        message = (
            "Jellyfin prewarm: metadata backfill could not list libraries "
            f"(HTTP {status}): {body}"
        )
        if required:
            raise RuntimeError(message)
        d.log(f"[WARN] {message}")
        return None

    @staticmethod
    def _library_context(
        library: dict[str, Any], libraries_filter: set[str],
    ) -> dict[str, str] | None:
        """Return ``{id, display_name, collection_type}`` or None to skip."""
        library_name = str(library.get("Name") or "").strip()
        library_id = str(library.get("ItemId") or "").strip()
        collection_type = str(library.get("CollectionType") or "").strip().lower()
        if not library_id:
            return None
        name_key = library_name.lower()
        if (
            libraries_filter
            and collection_type not in libraries_filter
            and name_key not in libraries_filter
        ):
            return None
        return {
            "id": library_id,
            "display_name": library_name or collection_type,
            "collection_type": collection_type,
        }

    def _refresh_library_items(
        self,
        *,
        d,
        jellyfin_url: str,
        jellyfin_api_key: str,
        opts: dict[str, Any],
        lib: dict[str, str],
        totals: dict[str, int],
    ) -> None:
        """Query one library's items, pick backfill targets, fire refreshes.

        Mutates ``totals`` in place so callers can aggregate across
        libraries without needing the full return shape.
        """
        rows = self._list_library_items(
            d=d, jellyfin_url=jellyfin_url, jellyfin_api_key=jellyfin_api_key,
            lib=lib, opts=opts,
        )
        if rows is None:
            return
        targets = self._pick_refresh_targets(
            rows,
            refresh_missing_primary=opts["refresh_missing_primary"],
            refresh_missing_overview=opts["refresh_missing_overview"],
            max_refresh_per_library=opts["max_refresh_per_library"],
        )
        if not targets:
            d.log(
                "[OK] Jellyfin prewarm: metadata backfill found no missing items "
                f"for {lib['display_name']}"
            )
            return
        library_requested, library_failed = self._fire_item_refreshes(
            d=d, jellyfin_url=jellyfin_url, jellyfin_api_key=jellyfin_api_key,
            targets=targets, refresh_params=opts["refresh_params"],
            display_name=lib["display_name"],
        )
        totals["candidates"] += len(targets)
        totals["requested"] += library_requested
        totals["failed"] += library_failed
        d.log(
            "[OK] Jellyfin prewarm: metadata backfill refresh requested "
            f"for {lib['display_name']} (targets={len(targets)}, "
            f"requested={library_requested}, failed={library_failed})"
        )

    @staticmethod
    def _fire_item_refreshes(
        *,
        d,
        jellyfin_url: str,
        jellyfin_api_key: str,
        targets: list[str],
        refresh_params: dict[str, Any],
        display_name: str,
    ) -> tuple[int, int]:
        """POST a refresh request for each target and tally success/failure."""
        library_requested, library_failed = 0, 0
        for item_id in targets:
            refresh_path = d.build_query_path(f"/Items/{item_id}/Refresh", refresh_params)
            status, _, body = d.jellyfin_request(
                jellyfin_url, refresh_path, jellyfin_api_key, method="POST",
            )
            if status in (200, 201, 202, 204):
                library_requested += 1
            else:
                library_failed += 1
                d.log(
                    "[WARN] Jellyfin prewarm: metadata backfill refresh failed "
                    f"for {display_name} item={item_id} (HTTP {status}): {body}"
                )
        return library_requested, library_failed

    @staticmethod
    def _list_library_items(
        *,
        d,
        jellyfin_url: str,
        jellyfin_api_key: str,
        lib: dict[str, str],
        opts: dict[str, Any],
    ) -> list | None:
        """Run the /Items query for a library; returns [] or None on failure.

        None signals the caller should skip the library entirely (error
        already logged or raised); an empty list is "query succeeded,
        no rows" and the caller should continue gracefully.
        """
        type_map = {
            "movies": ["Movie"],
            "tvshows": ["Series", "Episode"],
            "tv": ["Series", "Episode"],
            "books": ["Book"],
            "music": ["MusicAlbum", "MusicArtist", "Audio"],
        }
        include_types = type_map.get(lib["collection_type"]) or []
        list_path = d.build_query_path(
            "/Items",
            {
                "ParentId": lib["id"],
                "Recursive": "true",
                "IncludeItemTypes": ",".join(include_types) if include_types else None,
                "Fields": "ImageTags,PrimaryImageTag,PrimaryImageItemId,AlbumPrimaryImageTag,BackdropImageTags,Overview",
                "Limit": str(opts["sample_limit"]),
                "SortBy": "DateCreated",
                "SortOrder": "Descending",
            },
        )
        status, payload, body = d.jellyfin_request(jellyfin_url, list_path, jellyfin_api_key)
        if status != 200:
            message = (
                f"Jellyfin prewarm: metadata backfill query failed for {lib['display_name']} "
                f"(HTTP {status}): {body}"
            )
            if opts["required"]:
                raise RuntimeError(message)
            d.log(f"[WARN] {message}")
            return None
        if isinstance(payload, dict):
            items = payload.get("Items")
            return items if isinstance(items, list) else []
        if isinstance(payload, list):
            return payload
        return []

    @staticmethod
    def _pick_refresh_targets(
        rows: list,
        *,
        refresh_missing_primary: bool,
        refresh_missing_overview: bool,
        max_refresh_per_library: int,
    ) -> list[str]:
        """Return item IDs that are missing required artwork or overview."""
        targets: list[str] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("Id") or "").strip()
            if not item_id:
                continue
            needs_primary = refresh_missing_primary and (not item_has_artwork(item))
            needs_overview = refresh_missing_overview and (not item_has_overview(item))
            if not (needs_primary or needs_overview):
                continue
            targets.append(item_id)
            if len(targets) >= max_refresh_per_library:
                break
        return targets

    @staticmethod
    def _refresh_collection_folders(
        *,
        d,
        jellyfin_url: str,
        jellyfin_api_key: str,
        refresh_params: dict[str, Any],
        selected_libraries: list[tuple[str, str]],
        totals: dict[str, int],
    ) -> None:
        """Trigger a folder-level image refresh for each picked library."""
        for library_name, library_id in selected_libraries:
            totals["folders_attempted"] += 1
            refresh_path = d.build_query_path(f"/Items/{library_id}/Refresh", refresh_params)
            status, _, body = d.jellyfin_request(
                jellyfin_url, refresh_path, jellyfin_api_key, method="POST",
            )
            if status in (200, 201, 202, 204):
                totals["folders_refreshed"] += 1
            else:
                totals["folders_failed"] += 1
                d.log(
                    "[WARN] Jellyfin prewarm: collection-folder image refresh failed "
                    f"for {library_name} item={library_id} (HTTP {status}): {body}"
                )
        d.log(
            "[OK] Jellyfin prewarm: collection-folder image refresh complete "
            f"(attempted={totals['folders_attempted']}, "
            f"refreshed={totals['folders_refreshed']}, failed={totals['folders_failed']})"
        )

    def run_artwork_health_check(self, 
        service,
        prewarm_cfg: dict[str, Any],
        jellyfin_url: str,
        jellyfin_api_key: str,
    ) -> None:
        d = service.deps
        health_cfg = prewarm_cfg.get("artwork_health_check")
        if not isinstance(health_cfg, dict):
            health_cfg = {}
        if not d.bool_cfg(health_cfg, "enabled", True):
            return

        libraries_filter = {
            token.lower()
            for token in normalize_text_list(
                health_cfg.get("libraries"), ["Movies", "TV Shows", "Music", "Books", "Live TV"]
            )
        }
        try:
            max_items = int(health_cfg.get("max_items_per_library") or 400)
        except Exception:
            max_items = 400
        try:
            warn_below = float(health_cfg.get("warn_below_coverage_percent") or 70.0)
        except Exception:
            warn_below = 70.0
        try:
            fail_below = float(health_cfg.get("fail_below_coverage_percent") or 30.0)
        except Exception:
            fail_below = 30.0
        required = d.bool_cfg(health_cfg, "required", False)

        status, libraries_payload, body = d.jellyfin_request(
            jellyfin_url, "/Library/VirtualFolders", jellyfin_api_key
        )
        if status != 200 or not isinstance(libraries_payload, list):
            message = (
                "Jellyfin prewarm: artwork health check could not list libraries "
                f"(HTTP {status}): {body}"
            )
            if required:
                raise RuntimeError(message)
            d.log(f"[WARN] {message}")
            return

        type_map = {
            "movies": ["Movie"],
            "tvshows": ["Series", "Episode"],
            "tv": ["Series", "Episode"],
            "books": ["Book"],
            "music": ["MusicAlbum", "MusicArtist", "Audio"],
        }

        for library in libraries_payload:
            if not isinstance(library, dict):
                continue
            library_name = str(library.get("Name") or "").strip()
            library_id = str(library.get("ItemId") or "").strip()
            collection_type = str(library.get("CollectionType") or "").strip().lower()
            if not library_id:
                continue
            name_key = library_name.lower()
            if (
                libraries_filter
                and collection_type not in libraries_filter
                and name_key not in libraries_filter
            ):
                continue

            include_types = type_map.get(collection_type) or []
            path = d.build_query_path(
                "/Items",
                {
                    "ParentId": library_id,
                    "Recursive": "true",
                    "IncludeItemTypes": ",".join(include_types) if include_types else None,
                    "Fields": "ImageTags,PrimaryImageTag,PrimaryImageItemId,AlbumPrimaryImageTag,BackdropImageTags",
                    "Limit": str(max_items),
                    "SortBy": "DateCreated",
                    "SortOrder": "Descending",
                },
            )
            status, payload, body = d.jellyfin_request(jellyfin_url, path, jellyfin_api_key)
            if status != 200:
                message = (
                    f"Jellyfin prewarm: artwork health check query failed for {library_name or collection_type} "
                    f"(HTTP {status}): {body}"
                )
                if required:
                    raise RuntimeError(message)
                d.log(f"[WARN] {message}")
                continue

            if isinstance(payload, dict):
                items = payload.get("Items")
                rows = items if isinstance(items, list) else []
            elif isinstance(payload, list):
                rows = payload
            else:
                rows = []

            valid_items = [item for item in rows if isinstance(item, dict)]
            total = len(valid_items)
            if total == 0:
                d.log(
                    f"[INFO] Jellyfin prewarm: artwork health check skipped for {library_name} (no sampled items)"
                )
                continue
            with_art = sum(1 for item in valid_items if item_has_artwork(item))
            coverage = (with_art / total) * 100.0
            summary = f"Jellyfin prewarm: artwork coverage for {library_name} = {coverage:.1f}% ({with_art}/{total})"
            if coverage < fail_below and required:
                raise RuntimeError(f"{summary}; below fail threshold {fail_below:.1f}%")
            if coverage < warn_below:
                d.log(f"[WARN] {summary}; below warning threshold {warn_below:.1f}%")
            else:
                d.log(f"[OK] {summary}")

        if {"livetv", "live tv"} & libraries_filter:
            live_tv_path = d.build_query_path(
                "/LiveTv/Programs",
                {
                    "IsAiring": "true",
                    "Limit": str(max_items),
                    "Fields": "ImageTags,PrimaryImageTag,PrimaryImageItemId,BackdropImageTags",
                },
            )
            status, payload, body = d.jellyfin_request(jellyfin_url, live_tv_path, jellyfin_api_key)
            if status != 200:
                message = (
                    "Jellyfin prewarm: artwork health check query failed for Live TV "
                    f"(HTTP {status}): {body}"
                )
                if required:
                    raise RuntimeError(message)
                d.log(f"[WARN] {message}")
                return
            if isinstance(payload, dict):
                items = payload.get("Items")
                rows = items if isinstance(items, list) else []
            elif isinstance(payload, list):
                rows = payload
            else:
                rows = []

            valid_items = [item for item in rows if isinstance(item, dict)]
            total = len(valid_items)
            if total == 0:
                d.log(
                    "[INFO] Jellyfin prewarm: artwork health check skipped for Live TV (no sampled items)"
                )
                return
            with_art = sum(1 for item in valid_items if item_has_artwork(item))
            coverage = (with_art / total) * 100.0
            summary = (
                f"Jellyfin prewarm: artwork coverage for Live TV = {coverage:.1f}% ({with_art}/{total})"
            )
            if coverage < fail_below and required:
                raise RuntimeError(f"{summary}; below fail threshold {fail_below:.1f}%")
            if coverage < warn_below:
                d.log(f"[WARN] {summary}; below warning threshold {warn_below:.1f}%")
            else:
                d.log(f"[OK] {summary}")


_instance = JellyfinMetadataOps()
item_has_artwork = _instance.item_has_artwork
item_has_overview = _instance.item_has_overview
run_metadata_backfill = _instance.run_metadata_backfill
run_artwork_health_check = _instance.run_artwork_health_check
