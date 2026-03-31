"""Sonarr seed-series helpers for discovery list reconciliation."""

from __future__ import annotations

from typing import Any
from urllib import parse

from .common import normalize_title, pick_series_lookup_candidate


def resolve_series_quality_profile_id(
    service,
    app_name: str,
    app_url: str,
    api_base: str,
    api_key: str,
    seed_cfg: dict[str, Any],
) -> int | None:
    explicit = service.to_int(seed_cfg.get("quality_profile_id"))
    if explicit and explicit > 0:
        return int(explicit)

    status, profiles, body = service.http_request(app_url, f"{api_base}/qualityprofile", api_key=api_key)
    if status != 200 or not isinstance(profiles, list):
        raise RuntimeError(
            f"{app_name}: failed reading quality profiles for seed series (HTTP {status}): {body}"
        )

    preferred_tokens = [
        service.normalize_token(token)
        for token in service.coerce_list(seed_cfg.get("quality_profile_name_tokens"))
        if service.normalize_token(token)
    ]
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = service.to_int(profile.get("id"))
        name_token = service.normalize_token(profile.get("name"))
        if not profile_id:
            continue
        if preferred_tokens and any(token in name_token for token in preferred_tokens):
            return int(profile_id)

    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = service.to_int(profile.get("id"))
        if profile_id:
            return int(profile_id)
    return None


def resolve_series_language_profile_id(
    service,
    app_url: str,
    api_base: str,
    api_key: str,
    seed_cfg: dict[str, Any],
) -> int | None:
    explicit = service.to_int(seed_cfg.get("language_profile_id"))
    if explicit and explicit > 0:
        return int(explicit)

    status, profiles, _ = service.http_request(app_url, f"{api_base}/languageprofile", api_key=api_key)
    if status != 200 or not isinstance(profiles, list):
        return None
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = service.to_int(profile.get("id"))
        if profile_id:
            return int(profile_id)
    return None


def ensure_sonarr_seed_series(
    service,
    cfg: dict[str, Any],
    app_cfg: dict[str, Any],
    app_name: str,
    app_url: str,
    api_base: str,
    api_key: str,
    default_quality_profile_id: int | None,
) -> None:
    app_impl = str(app_cfg.get("implementation") or "").strip().lower()
    if app_impl != "sonarr":
        return

    seed_cfg = cfg.get("sonarr_seed_series")
    if not isinstance(seed_cfg, dict):
        return
    if not service.bool_cfg(seed_cfg, "enabled", True):
        return

    raw_names = service.coerce_list(seed_cfg.get("series"))
    seed_names = [str(item).strip() for item in raw_names if str(item).strip()]
    if not seed_names:
        service.log("[WARN] Sonarr seed series: enabled but no series names configured.")
        return

    max_series = service.to_int(seed_cfg.get("max_series"), len(seed_names))
    if max_series is not None and max_series <= 0:
        service.log("[OK] Sonarr seed series: max_series <= 0; skipping seed add.")
        return

    status, existing_series, body = service.http_request(app_url, f"{api_base}/series", api_key=api_key)
    if status != 200 or not isinstance(existing_series, list):
        raise RuntimeError(
            f"{app_name}: failed listing existing series for seed step (HTTP {status}): {body}"
        )

    existing_title_tokens = {
        normalize_title(item.get("title")) for item in existing_series if isinstance(item, dict)
    }
    existing_tvdb_ids = {
        int(tvdb_id)
        for tvdb_id in (
            service.to_int(item.get("tvdbId"))
            for item in existing_series
            if isinstance(item, dict)
        )
        if tvdb_id
    }

    quality_profile_id = default_quality_profile_id
    if not quality_profile_id:
        quality_profile_id = resolve_series_quality_profile_id(
            service=service,
            app_name=app_name,
            app_url=app_url,
            api_base=api_base,
            api_key=api_key,
            seed_cfg=seed_cfg,
        )
    if not quality_profile_id:
        raise RuntimeError(f"{app_name}: no quality profile id available for seed series.")

    language_profile_id = resolve_series_language_profile_id(
        service=service,
        app_url=app_url,
        api_base=api_base,
        api_key=api_key,
        seed_cfg=seed_cfg,
    )
    root_folder_path = str(seed_cfg.get("root_folder_path") or app_cfg.get("root_folder") or "").strip()
    if not root_folder_path:
        raise RuntimeError(f"{app_name}: root folder is required for seed series.")

    monitor_mode = str(seed_cfg.get("monitor") or "firstSeason").strip() or "firstSeason"
    season_folder = service.bool_cfg(seed_cfg, "season_folder", True)
    search_missing = service.bool_cfg(seed_cfg, "search_for_missing_episodes", True)

    created = 0
    skipped = 0
    failed = 0
    current_count = len(existing_series)

    for seed_name in seed_names:
        if max_series is not None and (current_count + created) >= int(max_series):
            break
        seed_token = normalize_title(seed_name)
        if seed_token in existing_title_tokens:
            skipped += 1
            continue

        lookup_path = f"{api_base}/series/lookup?term={parse.quote(seed_name)}"
        status, lookup_payload, body = service.http_request(app_url, lookup_path, api_key=api_key)
        if status != 200 or not isinstance(lookup_payload, list):
            service.log(f"[WARN] {app_name}: seed lookup failed for '{seed_name}' (HTTP {status}): {body}")
            failed += 1
            continue
        candidate = pick_series_lookup_candidate(lookup_payload, seed_name)
        if not candidate:
            service.log(f"[WARN] {app_name}: seed lookup returned no candidate for '{seed_name}'")
            failed += 1
            continue

        tvdb_id = service.to_int(candidate.get("tvdbId"))
        if not tvdb_id:
            service.log(f"[WARN] {app_name}: seed candidate missing tvdbId for '{seed_name}'")
            failed += 1
            continue
        if int(tvdb_id) in existing_tvdb_ids:
            skipped += 1
            continue

        payload: dict[str, Any] = {
            "title": candidate.get("title"),
            "titleSlug": candidate.get("titleSlug"),
            "tvdbId": int(tvdb_id),
            "images": candidate.get("images") or [],
            "qualityProfileId": int(quality_profile_id),
            "rootFolderPath": root_folder_path,
            "seasonFolder": bool(season_folder),
            "monitored": True,
            "addOptions": {
                "monitor": monitor_mode,
                "searchForMissingEpisodes": bool(search_missing),
                "searchForCutoffUnmetEpisodes": False,
            },
        }
        if language_profile_id:
            payload["languageProfileId"] = int(language_profile_id)

        status, _, body = service.http_request(
            app_url,
            f"{api_base}/series",
            api_key=api_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            created += 1
            existing_tvdb_ids.add(int(tvdb_id))
            existing_title_tokens.add(seed_token)
            service.log(
                f"[OK] {app_name}: seeded series '{candidate.get('title')}' "
                f"(monitor={monitor_mode}, search={bool(search_missing)})"
            )
        else:
            service.log(f"[WARN] {app_name}: failed seeding series '{seed_name}' (HTTP {status}): {body}")
            failed += 1

    service.log(
        f"[OK] {app_name}: seed series reconcile complete "
        f"(created={created}, skipped={skipped}, failed={failed}, max_series={max_series})"
    )
