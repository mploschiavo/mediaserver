"""Apply sensible runtime defaults to *arr apps after the
controller has wired credentials + indexers.

Background: the *arr apps ship with restrictive default quality
profiles that work fine for someone who hand-tunes them but reject
nearly every release on a fresh home install. This module bumps
the most common rejection patterns into "actually grabs stuff"
shape:

- **Radarr**: every quality profile defaults to ``language: {id:
  -2, name: 'Original'}`` which means "match the movie's TMDB
  original language". When TMDB metadata is wrong (e.g., calling
  the Mario Galaxy Movie Spanish-original), every English release
  gets rejected. Forcing language to ``Any`` (id ``-1``) lets the
  profile accept any language — the user can re-tighten if they
  care.

- **Lidarr**: the FLAC / lossless quality definitions inherit a
  cap of ~5 MB/min (great for MP3-320, terrible for FLAC). A
  60-min album ends up rejected at ~300 MB even though FLAC at
  that runtime is ~600 MB. Bump the FLAC family max to
  unlimited (``maxSize=null`` in Lidarr API parlance) and bump
  the MP3-320 / preferredSize so common high-bitrate releases
  don't trip the per-quality size guard.

- **Readarr**: the default eBook profile excludes the "Unknown
  Text" format, but most book indexers tag rare formats as
  Unknown. Allow it.

These patches are idempotent: each call inspects the current
state and skips when already correct."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

_log = logging.getLogger("media_stack.arr_runtime_defaults")


# Lidarr quality IDs (stable across versions) for FLAC / lossless
# / high-bitrate MP3 — the formats that legitimately blow past
# the MP3-friendly default size caps.
_LIDARR_UNLIMITED_QUALITY_NAMES = {
    "FLAC", "FLAC 24bit", "ALAC", "ALAC 24bit", "APE", "WavPack",
    "MP3-320", "MP3-VBR-V0", "AAC-VBR",
}


def patch_radarr_language_any(
    *,
    radarr_url: str,
    api_key: str,
    http_request: Callable,
    log: Callable[[str], None],
) -> int:
    """Force every Radarr quality profile to ``language: {id: -1,
    name: 'Any'}``. Returns the number of profiles updated.

    Idempotent: profiles already on Any are skipped."""
    status, profiles, body = http_request(
        radarr_url, "/api/v3/qualityprofile", api_key=api_key,
    )
    if status != 200 or not isinstance(profiles, list):
        log(
            f"[WARN] Radarr quality-defaults: profile fetch failed "
            f"(HTTP {status}): {str(body)[:200]}"
        )
        return 0

    any_lang = {"id": -1, "name": "Any"}
    updated = 0
    for prof in profiles:
        cur = prof.get("language") or {}
        if cur.get("id") == any_lang["id"]:
            continue
        prev_name = cur.get("name", "?")
        prof["language"] = any_lang
        pid = prof.get("id")
        if pid is None:
            continue
        st, _, b = http_request(
            radarr_url, f"/api/v3/qualityprofile/{pid}",
            api_key=api_key, method="PUT", payload=prof,
        )
        if st in (200, 202):
            updated += 1
            log(
                f"[OK] Radarr quality-defaults: profile '{prof.get('name')}' "
                f"language {prev_name} -> Any"
            )
        else:
            log(
                f"[WARN] Radarr quality-defaults: failed updating "
                f"profile {prof.get('name')} (HTTP {st}): {str(b)[:200]}"
            )
    return updated


def patch_lidarr_quality_sizes(
    *,
    lidarr_url: str,
    api_key: str,
    http_request: Callable,
    log: Callable[[str], None],
) -> int:
    """Bump Lidarr's per-quality max-size on FLAC / lossless /
    high-bitrate MP3 so legitimate album releases aren't rejected
    for being "too large" relative to MP3-128 expectations.

    Sets ``maxSize`` to ``None`` (unlimited) for FLAC + lossless
    families. Returns the number of definitions updated."""
    status, defs, body = http_request(
        lidarr_url, "/api/v1/qualitydefinition", api_key=api_key,
    )
    if status != 200 or not isinstance(defs, list):
        log(
            f"[WARN] Lidarr quality-defaults: defs fetch failed "
            f"(HTTP {status}): {str(body)[:200]}"
        )
        return 0

    updated = 0
    for d in defs:
        qname = (d.get("quality") or {}).get("name", "")
        if qname not in _LIDARR_UNLIMITED_QUALITY_NAMES:
            continue
        # Already unlimited?
        if d.get("maxSize") is None and d.get("preferredSize", 0) >= 500:
            continue
        new = dict(d)
        new["maxSize"] = None
        # Bump preferredSize so the upgrade-cutoff logic doesn't
        # think a 600 MB FLAC album is "too good" relative to a
        # 95 MB preferred. 1500 KB/s ≈ FLAC 16-bit average.
        new["preferredSize"] = 1500
        st, _, b = http_request(
            lidarr_url, f"/api/v1/qualitydefinition/{d.get('id')}",
            api_key=api_key, method="PUT", payload=new,
        )
        if st in (200, 202):
            updated += 1
            log(
                f"[OK] Lidarr quality-defaults: {qname} maxSize → unlimited "
                f"(was {d.get('maxSize')}, preferred 1500)"
            )
        else:
            log(
                f"[WARN] Lidarr quality-defaults: failed updating {qname} "
                f"(HTTP {st}): {str(b)[:200]}"
            )
    return updated


def patch_readarr_allow_unknown_text(
    *,
    readarr_url: str,
    api_key: str,
    http_request: Callable,
    log: Callable[[str], None],
) -> int:
    """Enable the ``Unknown Text`` format on the default eBook
    quality profile so loosely-tagged book indexer results aren't
    universally rejected. Returns 1 if it changed something."""
    status, profiles, body = http_request(
        readarr_url, "/api/v1/qualityprofile", api_key=api_key,
    )
    if status != 200 or not isinstance(profiles, list):
        log(
            f"[WARN] Readarr quality-defaults: profile fetch failed "
            f"(HTTP {status}): {str(body)[:200]}"
        )
        return 0

    updated = 0
    for prof in profiles:
        if str(prof.get("name", "")).lower() != "ebook":
            continue
        items = prof.get("items") or []
        changed = False
        for entry in items:
            qname = (entry.get("quality") or {}).get("name", "")
            if qname == "Unknown Text" and not entry.get("allowed"):
                entry["allowed"] = True
                changed = True
            for sub in (entry.get("items") or []):
                sname = (sub.get("quality") or {}).get("name", "")
                if sname == "Unknown Text" and not sub.get("allowed"):
                    sub["allowed"] = True
                    changed = True
        if not changed:
            continue
        pid = prof.get("id")
        st, _, b = http_request(
            readarr_url, f"/api/v1/qualityprofile/{pid}",
            api_key=api_key, method="PUT", payload=prof,
        )
        if st in (200, 202):
            updated += 1
            log("[OK] Readarr quality-defaults: eBook profile now allows Unknown Text")
        else:
            log(
                f"[WARN] Readarr quality-defaults: failed updating eBook profile "
                f"(HTTP {st}): {str(b)[:200]}"
            )
    return updated


def patch_arr_usenet_enabled(
    *,
    arr_url: str,
    api_ver: str,
    api_key: str,
    usenet_enabled: bool,
    http_request: Callable,
    log: Callable[[str], None],
) -> int:
    """Reconcile an *arr app's usenet configuration to the
    controller's ``download_clients.sabnzbd.configure_arr_clients``
    flag.

    When usenet is disabled:
      - Set every Sabnzbd download-client row to ``enable: false``.
      - Update delay profile(s) to ``preferredProtocol: torrent``
        so qBittorrent grabs fire immediately instead of waiting
        for a usenet client that will never succeed.

    When usenet is enabled:
      - Re-enable any disabled Sabnzbd client rows.
      - Set delay profile to ``preferredProtocol: usenet`` (the
        *arr out-of-box default).

    Idempotent; returns the count of rows updated."""
    updated = 0
    # 1. Download clients
    st, dcs, body = http_request(
        arr_url, f"/api/{api_ver}/downloadclient", api_key=api_key,
    )
    if st == 200 and isinstance(dcs, list):
        for dc in dcs:
            if (dc.get("implementation") or "").lower() != "sabnzbd":
                continue
            desired_enable = bool(usenet_enabled)
            if bool(dc.get("enable", False)) == desired_enable:
                continue
            dc["enable"] = desired_enable
            dcid = dc.get("id")
            st2, _, _ = http_request(
                arr_url, f"/api/{api_ver}/downloadclient/{dcid}",
                api_key=api_key, method="PUT", payload=dc,
            )
            if st2 in (200, 201, 202):
                updated += 1
                log(
                    f"[OK] *arr runtime-defaults: SABnzbd download "
                    f"client {'enabled' if desired_enable else 'disabled'}"
                )

    # 2. Delay profile
    st, profiles, _ = http_request(
        arr_url, f"/api/{api_ver}/delayprofile", api_key=api_key,
    )
    if st == 200 and isinstance(profiles, list):
        desired_proto = "usenet" if usenet_enabled else "torrent"
        for prof in profiles:
            if str(prof.get("preferredProtocol") or "") == desired_proto:
                continue
            prev = prof.get("preferredProtocol")
            prof["preferredProtocol"] = desired_proto
            pid = prof.get("id")
            st2, _, _ = http_request(
                arr_url, f"/api/{api_ver}/delayprofile/{pid}",
                api_key=api_key, method="PUT", payload=prof,
            )
            if st2 in (200, 201, 202):
                updated += 1
                log(
                    f"[OK] *arr runtime-defaults: delay profile "
                    f"preferredProtocol {prev} -> {desired_proto}"
                )
    return updated


def patch_arr_import_lists_auto(
    *,
    arr_url: str,
    api_ver: str,
    api_key: str,
    http_request: Callable,
    log: Callable[[str], None],
    impl: str,
) -> int:
    """Set ``enableAuto=True`` on every enabled import list.

    Operator-zero-touch: a fresh deploy with TMDb/Trakt lists already
    seeded ought to start fetching titles immediately, not require
    the user to click into Radarr/Sonarr settings and toggle each
    list. Idempotent — lists already at ``enableAuto=True`` are
    skipped, and disabled lists are left alone.

    Returns the number of lists updated."""
    status, lists, body = http_request(
        arr_url, f"/api/{api_ver}/importlist", api_key=api_key,
    )
    if status != 200 or not isinstance(lists, list):
        log(
            f"[WARN] {impl} import-lists-auto: fetch failed "
            f"(HTTP {status}): {str(body)[:200]}"
        )
        return 0

    updated = 0
    for il in lists:
        # Skip operator-disabled lists — turning auto on a list the
        # operator deliberately disabled would silently override
        # their intent.
        if not il.get("enabled", False):
            continue
        if il.get("enableAuto") is True:
            continue
        # Some Arr versions store the auto-toggle as ``enableAuto``,
        # some as ``enableAutomaticAdd``. Set both so the API
        # accepts the patch on either schema.
        new = dict(il)
        new["enableAuto"] = True
        if "enableAutomaticAdd" in new:
            new["enableAutomaticAdd"] = True
        lid = il.get("id")
        if lid is None:
            continue
        st, _, b = http_request(
            arr_url, f"/api/{api_ver}/importlist/{lid}",
            api_key=api_key, method="PUT", payload=new,
        )
        if st in (200, 202):
            updated += 1
            log(
                f"[OK] {impl} import-lists-auto: '{il.get('name')!s}' "
                "enableAuto False → True"
            )
        else:
            log(
                f"[WARN] {impl} import-lists-auto: failed updating "
                f"'{il.get('name')!s}' (HTTP {st}): {str(b)[:200]}"
            )
    return updated


def apply_arr_runtime_defaults(
    *,
    arr_apps: list[dict],
    app_keys: dict[str, str],
    service_url: Callable[[str], str],
    http_request: Callable,
    log: Callable[[str], None],
    usenet_enabled: bool | None = None,
) -> dict[str, int]:
    """Per-app dispatch.  Returns ``{app_name: updates_applied}``.

    Best-effort: any per-app failure is logged at WARN and the
    other apps still run.  Apps not present in ``arr_apps`` (e.g.
    Readarr disabled) are silently skipped."""
    summary: dict[str, int] = {}
    by_impl = {(a.get("implementation") or "").lower(): a for a in arr_apps}

    # Usenet-enabled gate: reconcile SAB download-client + delay
    # profile in every *arr to match the controller cfg. Applies
    # to all four *arr types.
    if usenet_enabled is not None:
        for impl, api_ver in (
            ("sonarr", "v3"), ("radarr", "v3"),
            ("lidarr", "v1"), ("readarr", "v1"),
        ):
            if impl not in by_impl or impl not in app_keys:
                continue
            try:
                patch_arr_usenet_enabled(
                    arr_url=service_url(impl),
                    api_ver=api_ver,
                    api_key=app_keys[impl],
                    usenet_enabled=bool(usenet_enabled),
                    http_request=http_request,
                    log=log,
                )
            except Exception as exc:
                log(f"[WARN] {impl} usenet-enabled reconcile: {exc}")

    if "radarr" in by_impl and "radarr" in app_keys:
        try:
            n = patch_radarr_language_any(
                radarr_url=service_url("radarr"),
                api_key=app_keys["radarr"],
                http_request=http_request,
                log=log,
            )
            summary["radarr"] = n
        except Exception as exc:
            log(f"[WARN] Radarr quality-defaults: {exc}")

    # Auto-add toggle for import lists: applies to sonarr/radarr/
    # lidarr/readarr uniformly. Each *arr's import-list endpoint is
    # at /api/v{3,3,1,1}/importlist and uses ``enableAuto`` (newer)
    # / ``enableAutomaticAdd`` (older). Skipping when the list is
    # operator-disabled preserves explicit "off" intent.
    for impl, api_ver in (
        ("sonarr", "v3"), ("radarr", "v3"),
        ("lidarr", "v1"), ("readarr", "v1"),
    ):
        if impl not in by_impl or impl not in app_keys:
            continue
        try:
            n = patch_arr_import_lists_auto(
                arr_url=service_url(impl),
                api_ver=api_ver,
                api_key=app_keys[impl],
                http_request=http_request,
                log=log,
                impl=impl,
            )
            if n:
                summary[f"{impl}_import_lists"] = n
        except Exception as exc:
            log(f"[WARN] {impl} import-lists-auto: {exc}")

    if "lidarr" in by_impl and "lidarr" in app_keys:
        try:
            n = patch_lidarr_quality_sizes(
                lidarr_url=service_url("lidarr"),
                api_key=app_keys["lidarr"],
                http_request=http_request,
                log=log,
            )
            summary["lidarr"] = n
        except Exception as exc:
            log(f"[WARN] Lidarr quality-defaults: {exc}")

    if "readarr" in by_impl and "readarr" in app_keys:
        try:
            n = patch_readarr_allow_unknown_text(
                readarr_url=service_url("readarr"),
                api_key=app_keys["readarr"],
                http_request=http_request,
                log=log,
            )
            summary["readarr"] = n
        except Exception as exc:
            log(f"[WARN] Readarr quality-defaults: {exc}")

    return summary
