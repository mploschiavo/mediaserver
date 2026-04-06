"""Metadata/artwork prewarm helpers for Jellyfin."""

from __future__ import annotations

from typing import Any

from .sidecar_ops import normalize_text_list


def item_has_artwork(item: dict[str, Any]) -> bool:
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


def item_has_overview(item: dict[str, Any]) -> bool:
    return bool(str(item.get("Overview") or "").strip())


def run_metadata_backfill(
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

    libraries_filter = {
        token.lower()
        for token in normalize_text_list(
            backfill_cfg.get("libraries"), ["Movies", "TV Shows", "Music", "Books"]
        )
    }
    refresh_missing_primary = d.bool_cfg(backfill_cfg, "refresh_missing_primary_image", True)
    refresh_missing_overview = d.bool_cfg(backfill_cfg, "refresh_missing_overview", True)
    refresh_collection_folders = d.bool_cfg(backfill_cfg, "refresh_collection_folder_images", True)
    required = d.bool_cfg(backfill_cfg, "required", False)
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

    status, libraries_payload, body = d.jellyfin_request(
        jellyfin_url, "/Library/VirtualFolders", jellyfin_api_key
    )
    if status != 200 or not isinstance(libraries_payload, list):
        message = (
            "Jellyfin prewarm: metadata backfill could not list libraries "
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

    total_candidates = 0
    total_requested = 0
    total_failed = 0
    folders_attempted = 0
    folders_refreshed = 0
    folders_failed = 0
    selected_libraries: list[tuple[str, str]] = []

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
        selected_libraries.append((library_name or collection_type, library_id))

        include_types = type_map.get(collection_type) or []
        list_path = d.build_query_path(
            "/Items",
            {
                "ParentId": library_id,
                "Recursive": "true",
                "IncludeItemTypes": ",".join(include_types) if include_types else None,
                "Fields": "ImageTags,PrimaryImageTag,PrimaryImageItemId,AlbumPrimaryImageTag,BackdropImageTags,Overview",
                "Limit": str(sample_limit),
                "SortBy": "DateCreated",
                "SortOrder": "Descending",
            },
        )
        status, payload, body = d.jellyfin_request(jellyfin_url, list_path, jellyfin_api_key)
        if status != 200:
            message = (
                f"Jellyfin prewarm: metadata backfill query failed for {library_name or collection_type} "
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

        if not targets:
            d.log(
                "[OK] Jellyfin prewarm: metadata backfill found no missing items "
                f"for {library_name}"
            )
            continue

        library_requested = 0
        library_failed = 0
        for item_id in targets:
            refresh_path = d.build_query_path(f"/Items/{item_id}/Refresh", refresh_params)
            status, _, body = d.jellyfin_request(
                jellyfin_url,
                refresh_path,
                jellyfin_api_key,
                method="POST",
            )
            if status in (200, 201, 202, 204):
                library_requested += 1
            else:
                library_failed += 1
                d.log(
                    "[WARN] Jellyfin prewarm: metadata backfill refresh failed "
                    f"for {library_name} item={item_id} (HTTP {status}): {body}"
                )

        total_candidates += len(targets)
        total_requested += library_requested
        total_failed += library_failed
        d.log(
            "[OK] Jellyfin prewarm: metadata backfill refresh requested "
            f"for {library_name} (targets={len(targets)}, requested={library_requested}, failed={library_failed})"
        )

    if refresh_collection_folders:
        for library_name, library_id in selected_libraries:
            folders_attempted += 1
            refresh_path = d.build_query_path(f"/Items/{library_id}/Refresh", refresh_params)
            status, _, body = d.jellyfin_request(
                jellyfin_url,
                refresh_path,
                jellyfin_api_key,
                method="POST",
            )
            if status in (200, 201, 202, 204):
                folders_refreshed += 1
            else:
                folders_failed += 1
                d.log(
                    "[WARN] Jellyfin prewarm: collection-folder image refresh failed "
                    f"for {library_name} item={library_id} (HTTP {status}): {body}"
                )
        d.log(
            "[OK] Jellyfin prewarm: collection-folder image refresh complete "
            f"(attempted={folders_attempted}, refreshed={folders_refreshed}, failed={folders_failed})"
        )

    if (total_failed or folders_failed) and required:
        raise RuntimeError(
            "Jellyfin prewarm: metadata backfill had refresh failures "
            f"(requested={total_requested}, failed={total_failed}, folder_failed={folders_failed})"
        )
    d.log(
        "[OK] Jellyfin prewarm: metadata backfill complete "
        f"(candidates={total_candidates}, requested={total_requested}, failed={total_failed}, "
        f"folder_refreshed={folders_refreshed}, folder_failed={folders_failed})"
    )


def run_artwork_health_check(
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
