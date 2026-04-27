"""Content services: library stats, downloads, indexers, versions, history."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from media_stack.services.apps.download_clients.registry_helpers import DOWNLOAD_CLIENT_CATEGORIES
from .content_analytics_mixin import _ContentAnalyticsMixin
from .content_download_settings_mixin import _ContentDownloadSettingsMixin
from .health import discover_api_keys
from .registry import SERVICE_MAP, SERVICES
from .runtime_keys import read_service_api_key
import logging

# HTTP timeouts. Two bands cover the call sites in this module:
#   PROBE  — quick liveness / health checks (default for most calls)
#   GET    — paginated lists or anything with a known-slow upstream
# Operators tune both in one place rather than chasing per-call
# ``timeout=N`` literals across 700+ lines.
_HTTP_PROBE_TIMEOUT_S = 5
_HTTP_GET_TIMEOUT_S = 10
_HTTP_QUICK_TIMEOUT_S = 4


# Extracted to remove 5+ duplicate string-literal warnings (duplicate-strings
# ratchet). Content-Type / Accept header values are canonical JSON MIME —
# defining once makes downstream edits (e.g. switching to ``application/vnd+``
# variants) a single-line change.
_JSON_MIME = "application/json"


def _cutoff_name_from_items(items: list[Any], cutoff_id: Any) -> str:
    """Resolve a quality-profile cutoff id to its display name.

    Pulled out of ``get_quality_profiles`` to flatten a deeply nested
    search (for/if/for/if) into a single pass; behaviour is identical:
    the first match — whether on an outer group or a nested quality —
    wins. Falls back to ``str(cutoff_id)`` when no entry matches.
    """
    default = str(cutoff_id)
    for item in items:
        if item.get("id") == cutoff_id:
            return item.get("name", default)
        for q in item.get("items", []):
            if q.get("id") == cutoff_id:
                return q.get("name", default)
    return default


def _find_scan_task(tasks: list[Any]) -> dict[str, Any] | None:
    """Locate the Jellyfin scheduled task named ``Scan Media``.

    Hoisted out of ``get_download_client_settings`` to flatten the
    for/if/if/if ladder; returns ``None`` if no matching task exists
    rather than letting the caller nest another level of ``if``.
    """
    for t in tasks:
        if isinstance(t, dict) and "Scan Media" in t.get("Name", ""):
            return t
    return None


def _summarize_scan_task(task: dict[str, Any]) -> dict[str, Any]:
    """Shape a Jellyfin scheduled-task payload for the dashboard.

    Decodes the 100-ns ``IntervalTicks`` into hours. Extracted so the
    outer handler reads as a single ``if task: result = summarize(...)``
    instead of a 4+-deep ladder.
    """
    triggers = task.get("Triggers", [])
    interval_h = 12
    if triggers:
        ticks = triggers[0].get("IntervalTicks", 0)
        if ticks:
            interval_h = int(ticks / 36000000000)
    return {
        "task_id": task.get("Id", ""),
        "state": task.get("State", "?"),
        "interval_hours": interval_h,
        "last_status": task.get("LastExecutionResult", {}).get("Status", "never"),
    }


def _update_jellyfin_scan_interval(scan_interval: Any) -> dict[str, Any]:
    """Write a new ``IntervalTicks`` trigger onto Jellyfin's Scan Media task.

    Extracted so ``update_download_client_settings`` doesn't need 4
    levels of if/try/for/if nesting. Returns a status dict suitable
    for merging into the caller's response.
    """
    try:
        api_key = discover_api_keys().get("jellyfin", "")
        ms = SERVICE_MAP.get("jellyfin")
        if not (ms and api_key):
            return {"status": "skipped", "reason": "jellyfin not reachable"}
        tasks = json.loads(urllib.request.urlopen(
            f"http://{ms.host}:{ms.port}/ScheduledTasks?api_key={api_key}", timeout=_HTTP_PROBE_TIMEOUT_S,
        ).read())
        task = _find_scan_task(tasks)
        if task is None:
            return {"status": "skipped", "reason": "Scan Media task missing"}
        ticks = int(scan_interval) * 36000000000
        triggers = [{"Type": "IntervalTrigger", "IntervalTicks": ticks}]
        req = urllib.request.Request(
            f"http://{ms.host}:{ms.port}/ScheduledTasks/{task['Id']}/Triggers?api_key={api_key}",
            data=json.dumps(triggers).encode(),
            method="POST",
            headers={"Content-Type": _JSON_MIME},
        )
        urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S)
        return {"status": "updated", "hours": int(scan_interval)}
    except Exception as exc:
        return {"error": str(exc)[:80]}


def _pick_poster_url(images: Any) -> str:
    """Extract a poster URL from an arr `images` array.

    radarr/sonarr/lidarr/readarr return per-item images as
    ``[{coverType: "poster"|"fanart"|"banner"|..., url, remoteUrl}, ...]``.
    Prefer ``coverType=="poster"``, then ``"cover"``, then the first
    entry. ``url`` is the local arr-served path; ``remoteUrl`` points
    at TMDB/TVDB. We return ``url`` first because it stays valid even
    when the upstream metadata source is throttled or the item has
    been deleted from TMDB. Empty string when no images are present
    so the UI's ``item.poster ? <img/> : <placeholder/>`` branch
    works cleanly.
    """
    if not isinstance(images, list):
        return ""
    by_type: dict[str, str] = {}
    first_url = ""
    for entry in images:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or entry.get("remoteUrl") or "")
        if not url:
            continue
        if not first_url:
            first_url = url
        cover = str(entry.get("coverType") or "").lower()
        if cover and cover not in by_type:
            by_type[cover] = url
    return by_type.get("poster") or by_type.get("cover") or first_url


def _profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Flatten a single arr quality-profile payload into a dashboard shape.

    Exists to pull a 5-deep loop out of ``get_quality_profiles`` — see
    ``_cutoff_name_from_items`` for the cutoff-resolution specifics.
    """
    entry: dict[str, Any] = {"id": profile.get("id"), "name": profile.get("name", "")}
    if "upgradeAllowed" in profile:
        entry["upgradeAllowed"] = profile["upgradeAllowed"]
    cutoff_id = profile.get("cutoff")
    if cutoff_id is not None:
        entry["cutoff"] = _cutoff_name_from_items(profile.get("items", []), cutoff_id)
    return entry


class ContentService(_ContentAnalyticsMixin, _ContentDownloadSettingsMixin):
    """Content operations: library stats, downloads, indexers, versions, history.

    Several surface areas live on sibling mixins so the class body
    stays under the 500-line god-class ratchet — same public methods,
    same behaviour, just split by topic:

      * ``_ContentAnalyticsMixin`` — ``get_download_analytics``,
        ``ensure_arr_scan_webhooks``.
      * ``_ContentDownloadSettingsMixin`` — qBittorrent + Jellyfin
        scan control panel (``get_download_client_settings`` /
        ``update_download_client_settings``).
    """

    def get_versions(self, cache: Any) -> dict[str, Any]:
        """Fetch version strings from arr apps and other services."""
        cached = cache.get("versions", 300)
        if cached is not None:
            return cached

        api_keys = discover_api_keys()
        version_endpoints: dict[str, tuple[str, int, str, str]] = {
            s.id: (s.host, s.port, s.version_path, s.version_json_key)
            for s in SERVICES if s.version_path
        }

        def fetch_version(name: str) -> tuple[str, str]:
            host, port, path, json_key = version_endpoints[name]
            key = api_keys.get(name, "")
            headers: dict[str, str] = {"Accept": _JSON_MIME}
            if key:
                headers["X-Api-Key"] = key
            try:
                req = urllib.request.Request(f"http://{host}:{port}{path}", headers=headers)
                with urllib.request.urlopen(req, timeout=_HTTP_QUICK_TIMEOUT_S) as resp:
                    data = json.loads(resp.read())
                    parts = json_key.split(".")
                    val = data
                    for p in parts:
                        val = val.get(p) if isinstance(val, dict) else None
                    return name, str(val or "?")
            except Exception:
                return name, ""

        versions: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(fetch_version, n) for n in version_endpoints]
            for f in as_completed(futures):
                try:
                    n, v = f.result()
                    if v:
                        versions[n] = v
                except Exception as exc:
                    log_swallowed(exc)
        result = {"versions": versions}
        cache.set("versions", result)
        return result

    def get_downloads(self) -> dict[str, Any]:
        """Fetch active downloads from registered download client services."""
        result: dict[str, Any] = {}
        for svc_id, category in _DOWNLOAD_CLIENT_IDS.items():
            svc = SERVICE_MAP.get(svc_id)
            if not svc or not svc.host or not svc.port:
                continue
            fetcher = _DOWNLOAD_FETCHERS.get(category)
            if not fetcher:
                continue
            try:
                result[svc_id] = fetcher(svc.host, svc.port)
            except Exception as exc:
                result[svc_id] = {"active": 0, "items": [], "error": str(exc)[:60]}
        return result

    def get_stats(self, cache: Any) -> dict[str, Any]:
        """Fetch library counts from arr apps."""
        cached = cache.get("stats", 60)
        if cached is not None:
            return cached
        api_keys = discover_api_keys()
        apps = [
            (s.id, s.host, s.port, s.stats_path, s.stats_label)
            for s in SERVICES if s.stats_path
        ]

        def fetch_count(name: str) -> tuple[str, dict[str, Any]]:
            host, port, path, label = [(a[1], a[2], a[3], a[4]) for a in apps if a[0] == name][0]
            key = api_keys.get(name, "")
            if not key:
                return name, {"count": 0, "label": label}
            try:
                req = urllib.request.Request(f"http://{host}:{port}{path}", headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                    data = json.loads(resp.read())
                return name, {"count": len(data) if isinstance(data, list) else 0, "label": label}
            except Exception as exc:
                return name, {"count": 0, "label": label, "error": str(exc)[:60]}

        stats: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetch_count, a[0]) for a in apps]
            for f in as_completed(futures):
                try:
                    n, v = f.result()
                    stats[n] = v
                except Exception as exc:
                    log_swallowed(exc)
        result = {"stats": stats}
        cache.set("stats", result)
        return result

    def get_indexers(self) -> dict[str, Any]:
        """Fetch indexer list from services with indexer_path."""
        api_keys = discover_api_keys()
        indexer_services = [s for s in SERVICES if s.indexer_path]
        if not indexer_services:
            return {"indexers": [], "total": 0, "enabled": 0}
        svc = indexer_services[0]
        key = api_keys.get(svc.id, "")
        if not key:
            return {"indexers": [], "total": 0, "enabled": 0}
        try:
            req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}{svc.indexer_path}",
                headers={"X-Api-Key": key},
            )
            with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                data = json.loads(resp.read())
            indexers = [
                {"id": i.get("id"), "name": i.get("name", ""), "enable": i.get("enable", False),
                 "protocol": i.get("protocol", "")}
                for i in data
            ]
            enabled = sum(1 for i in indexers if i["enable"])
            return {"indexers": indexers, "total": len(indexers), "enabled": enabled}
        except Exception:
            return {"indexers": [], "total": 0, "enabled": 0}

    def get_indexer_stats(self) -> dict[str, Any]:
        """Fetch indexer performance stats from services with indexer_stats_path."""
        api_keys = discover_api_keys()
        stats_services = [s for s in SERVICES if s.indexer_stats_path]
        if not stats_services:
            return {"stats": []}
        svc = stats_services[0]
        key = api_keys.get(svc.id, "")
        if not key:
            return {"stats": []}
        try:
            req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}{svc.indexer_stats_path}",
                headers={"X-Api-Key": key},
            )
            with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                data = json.loads(resp.read())
            stats = data.get("indexers", data) if isinstance(data, dict) else data
            return {"stats": stats if isinstance(stats, list) else []}
        except Exception:
            return {"stats": []}

    def get_download_history(self) -> dict[str, Any]:
        """Fetch recent download history from arr apps."""
        api_keys = discover_api_keys()
        apps = [
            (s.id, s.host, s.port, s.history_path)
            for s in SERVICES if s.history_path
        ]

        def fetch(name: str) -> tuple[str, list[dict[str, str]]]:
            host, port, path = [(a[1], a[2], a[3]) for a in apps if a[0] == name][0]
            key = api_keys.get(name, "")
            if not key:
                return name, []
            try:
                req = urllib.request.Request(f"http://{host}:{port}{path}", headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                    data = json.loads(resp.read())
                records = data.get("records", data) if isinstance(data, dict) else data
                return name, [
                    {"title": r.get("sourceTitle", "")[:60], "event": r.get("eventType", ""),
                     "date": str(r.get("date", ""))[:19]}
                    for r in (records if isinstance(records, list) else [])[:10]
                ]
            except Exception:
                return name, []

        history: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            for f in as_completed([pool.submit(fetch, a[0]) for a in apps]):
                try:
                    n, v = f.result()
                    history[n] = v
                except Exception as exc:
                    log_swallowed(exc)
        return {"history": history}

    def get_quality_profiles(self) -> dict[str, Any]:
        """Fetch quality profiles from arr apps."""
        api_keys = discover_api_keys()
        apps = [
            (s.id, s.host, s.port, s.quality_profile_path)
            for s in SERVICES if s.quality_profile_path
        ]

        def fetch(name: str) -> tuple[str, list[dict[str, Any]]]:
            host, port, path = [(a[1], a[2], a[3]) for a in apps if a[0] == name][0]
            key = api_keys.get(name, "")
            if not key:
                return name, []
            try:
                req = urllib.request.Request(f"http://{host}:{port}{path}", headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                    data = json.loads(resp.read())
                if not isinstance(data, list):
                    return name, []
                return name, [_profile_summary(p) for p in data]
            except Exception:
                return name, []

        profiles: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            for f in as_completed([pool.submit(fetch, a[0]) for a in apps]):
                try:
                    n, v = f.result()
                    profiles[n] = v
                except Exception as exc:
                    log_swallowed(exc)
        return {"profiles": profiles}

    def get_import_lists(self) -> dict[str, Any]:
        """Fetch import/discovery lists from arr apps."""
        api_keys = discover_api_keys()
        apps = [
            (s.id, s.host, s.port, s.import_list_path)
            for s in SERVICES if s.import_list_path
        ]

        def fetch(name: str) -> tuple[str, list[dict[str, Any]]]:
            host, port, path = [(a[1], a[2], a[3]) for a in apps if a[0] == name][0]
            key = api_keys.get(name, "")
            if not key:
                return name, []
            try:
                req = urllib.request.Request(f"http://{host}:{port}{path}", headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                    data = json.loads(resp.read())
                return name, [
                    {"id": i.get("id"), "name": i.get("name", ""), "enabled": i.get("enableAutomaticAdd"),
                     "listType": i.get("listType", "")}
                    for i in data
                ] if isinstance(data, list) else []
            except Exception:
                return name, []

        lists: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            for f in as_completed([pool.submit(fetch, a[0]) for a in apps]):
                try:
                    n, v = f.result()
                    lists[n] = v
                except Exception as exc:
                    log_swallowed(exc)
        return {"lists": lists}

    def get_media_server_libraries(self) -> dict[str, Any]:
        """Fetch library list from the active media server (registry-driven).

        Uses ``read_service_api_key`` so an empty K8s Secret (typical on
        first boot, before ``discover-api-keys`` has populated it)
        triggers a fallback to the on-disk config file rather than
        silently returning ``{libraries: []}``. When neither source has
        a key the response includes a structured ``error`` field so the
        dashboard can render an actionable message instead of "0 of
        each".
        """
        first_seen: str | None = None
        for svc in SERVICES:
            # Registry uses category="media" (was "media-server" in an
            # earlier registry shape — the rename was never propagated
            # here). With the stale string, this loop matched zero
            # services, the function returned {libraries: []}, and the
            # `/api/libraries` handler reported `live: []` + `source:
            # defaults`. The dashboard's banner mis-attributed that to a
            # missing JELLYFIN_API_KEY, even though the env was fine and
            # Jellyfin was reachable. Iteration order (alphabetical id)
            # plus the `if not key: continue` guard means we hit emby
            # (no api_key_env, skipped) → jellyfin (success, return) →
            # never reach jellyseerr/mythtv/plex.
            if svc.category != "media" or not svc.host or not svc.port:
                continue
            first_seen = first_seen or svc.id
            key = read_service_api_key(svc.id)
            if not key:
                continue
            try:
                # Emby/Jellyfin use X-Emby-Token, Plex uses X-Plex-Token
                auth_header = "X-Emby-Token" if svc.auth_mode == "X-Emby-Token" else svc.auth_mode
                req = urllib.request.Request(
                    f"http://{svc.host}:{svc.port}/Library/VirtualFolders",
                    headers={auth_header: key},
                )
                with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                    data = json.loads(resp.read())
                libs: list[dict[str, Any]] = []
                for lib in (data if isinstance(data, list) else []):
                    ctype = lib.get("CollectionType") or ""
                    libs.append({
                        # Field names match the UI's `LiveLibraryEntry`
                        # contract (collection_type/item_count, not
                        # type/count). Without these names the
                        # LibraryStatsTiles fallback path fires and
                        # the dashboard shows "1 1 1 1" (configured-
                        # libraries-per-type) instead of real counts.
                        "name": lib.get("Name", ""),
                        "collection_type": ctype,
                        "paths": lib.get("Locations", []),
                        # /Library/VirtualFolders.ItemCount is metadata
                        # and is `null` even on populated libraries
                        # (confirmed against Jellyfin 10.x). Query
                        # /Items.TotalRecordCount per library for the
                        # authoritative count.
                        "item_count": self._jellyfin_library_item_count(
                            svc.host, svc.port, auth_header, key,
                            parent_id=str(lib.get("ItemId") or ""),
                            collection_type=ctype,
                        ),
                    })
                return {"libraries": libs}
            except Exception as exc:
                return {"libraries": [], "error": f"{svc.id}: {str(exc)[:80]}"}
        if first_seen:
            return {
                "libraries": [],
                "error": f"no API key for {first_seen}",
            }
        return {"libraries": []}

    # Map Jellyfin CollectionType → IncludeItemTypes for the per-library
    # /Items count query. "music" → "Audio" (songs/tracks) matches the
    # dashboard's "Tracks" tile label; "tvshows" → "Series" mirrors the
    # convention of counting shows-not-episodes for a library overview.
    _JELLYFIN_ITEM_TYPE_FOR: dict[str, str] = {
        "movies": "Movie",
        "tvshows": "Series",
        "music": "Audio",
        "books": "Book",
        "boxsets": "BoxSet",
    }

    def _jellyfin_library_item_count(
        self, host: str, port: int, auth_header: str, key: str,
        parent_id: str, collection_type: str,
    ) -> int:
        """Return TotalRecordCount for one Jellyfin library.

        /Library/VirtualFolders.ItemCount is null on production deploys
        even for libraries that contain content; /Items?Recursive=true
        &Limit=0 returns the authoritative count via TotalRecordCount.
        Fails closed to 0 on any error so a single library outage
        doesn't take the whole list down."""
        item_type = self._JELLYFIN_ITEM_TYPE_FOR.get(collection_type, "")
        params = ["Recursive=true", "Limit=0"]
        if item_type:
            params.append(f"IncludeItemTypes={item_type}")
        if parent_id:
            params.append(f"ParentId={parent_id}")
        try:
            req = urllib.request.Request(
                f"http://{host}:{port}/Items?{'&'.join(params)}",
                headers={auth_header: key},
            )
            with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                data = json.loads(resp.read())
            total = data.get("TotalRecordCount")
            return int(total) if isinstance(total, (int, float)) else 0
        except Exception as exc:
            log_swallowed(exc)
            return 0

    def get_recent(self) -> dict[str, Any]:
        """Fetch recently added items from arr apps."""
        api_keys = discover_api_keys()
        apps = [
            (s.id, s.host, s.port, s.recent_path)
            for s in SERVICES if s.recent_path
        ]

        def fetch_recent(name: str) -> tuple[str, list[dict[str, str]]]:
            host, port, path = [(a[1], a[2], a[3]) for a in apps if a[0] == name][0]
            key = api_keys.get(name, "")
            if not key:
                return name, []
            try:
                req = urllib.request.Request(f"http://{host}:{port}{path}", headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                    data = json.loads(resp.read())
                items = data[:5] if isinstance(data, list) else []
                # arr APIs (radarr v3 / sonarr v3 / lidarr / readarr)
                # use `added` (ISO datetime) — the older `dateAdded`
                # is a Sonarr v2 / *-arr legacy field. Try `added`
                # first, fall back to `dateAdded` for any service
                # still on the older shape.
                #
                # Posters come from the `images` array, each entry
                # is {coverType: "poster"|"fanart"|..., url, remoteUrl}.
                # Prefer the poster cover type; fall back to the first
                # image. The UI's RecentAdditionsCard reads `poster`
                # (the canonical name in features/library/hooks.ts).
                return name, [
                    {
                        "title": i.get("title", ""),
                        "added": str(i.get("added") or i.get("dateAdded") or "")[:10],
                        "poster": _pick_poster_url(i.get("images")),
                    }
                    for i in items
                ]
            except Exception:
                return name, []

        recent: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            for f in as_completed([pool.submit(fetch_recent, a[0]) for a in apps]):
                try:
                    n, v = f.result()
                    recent[n] = v
                except Exception as exc:
                    log_swallowed(exc)
        return {"recent": recent}

    # -----------------------------------------------------------------------
    # Indexer management — enable/disable, manual add (via Prowlarr API)
    # -----------------------------------------------------------------------

    def toggle_indexer(self, indexer_id: int, enable: bool) -> dict[str, Any]:
        """Enable or disable a specific indexer by ID."""
        api_keys = discover_api_keys()
        svc = next((s for s in SERVICES if s.indexer_path), None)
        if not svc:
            return {"error": "No indexer manager service configured"}
        key = api_keys.get(svc.id, "")
        if not key:
            return {"error": f"No API key for {svc.id}"}
        try:
            # GET current indexer config
            req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}{svc.indexer_path}/{indexer_id}",
                headers={"X-Api-Key": key},
            )
            with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                indexer = json.loads(resp.read())
            indexer["enable"] = enable
            # PUT updated config
            put_req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}{svc.indexer_path}/{indexer_id}",
                data=json.dumps(indexer).encode(), method="PUT",
                headers={"X-Api-Key": key, "Content-Type": _JSON_MIME},
            )
            urllib.request.urlopen(put_req, timeout=_HTTP_PROBE_TIMEOUT_S)
            return {"status": "ok", "indexer_id": indexer_id, "enable": enable}
        except Exception as exc:
            return {"error": str(exc)[:120]}

    def delete_indexer(self, indexer_id: int) -> dict[str, Any]:
        """Delete an indexer by ID."""
        api_keys = discover_api_keys()
        svc = next((s for s in SERVICES if s.indexer_path), None)
        if not svc:
            return {"error": "No indexer manager service configured"}
        key = api_keys.get(svc.id, "")
        if not key:
            return {"error": f"No API key for {svc.id}"}
        try:
            req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}{svc.indexer_path}/{indexer_id}",
                method="DELETE", headers={"X-Api-Key": key},
            )
            urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S)
            return {"status": "deleted", "indexer_id": indexer_id}
        except Exception as exc:
            return {"error": str(exc)[:120]}

    # -----------------------------------------------------------------------
    # Import list management — add/remove Trakt/IMDb/RSS lists (via Arr APIs)
    # -----------------------------------------------------------------------

    def get_all_import_lists(self) -> dict[str, Any]:
        """Fetch import lists from all arr services that support them."""
        api_keys = discover_api_keys()
        apps = [(s.id, s.host, s.port, s.import_list_path) for s in SERVICES if s.import_list_path]
        all_lists: dict[str, list] = {}
        for svc_id, host, port, path in apps:
            key = api_keys.get(svc_id, "")
            if not key:
                continue
            try:
                req = urllib.request.Request(f"http://{host}:{port}{path}", headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
                    data = json.loads(resp.read())
                all_lists[svc_id] = [
                    {"id": l.get("id"), "name": l.get("name", ""), "listType": l.get("listType", ""),
                     "enabled": l.get("enableAuto", True)}
                    for l in (data if isinstance(data, list) else [])
                ]
            except Exception:
                all_lists[svc_id] = []
        return {"lists": all_lists, "total": sum(len(v) for v in all_lists.values())}

    # get_download_analytics lives on ``_ContentAnalyticsMixin``.

    def toggle_import_list(self, service_id: str, list_id: int, enabled: bool) -> dict[str, Any]:
        """Enable or disable an import list on a specific arr service."""
        api_keys = discover_api_keys()
        svc = SERVICE_MAP.get(service_id)
        if not svc or not svc.import_list_path:
            return {"error": f"Service '{service_id}' not found or has no import list support"}
        key = api_keys.get(service_id, "")
        if not key:
            return {"error": f"No API key for {service_id}"}
        try:
            # GET current list, flip enabled, PUT back
            url = f"http://{svc.host}:{svc.port}{svc.import_list_path}/{list_id}"
            req = urllib.request.Request(url, headers={"X-Api-Key": key})
            with urllib.request.urlopen(req, timeout=_HTTP_GET_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode())
            data["enabled"] = enabled
            put_req = urllib.request.Request(
                url, data=json.dumps(data).encode(),
                method="PUT", headers={"X-Api-Key": key, "Content-Type": _JSON_MIME},
            )
            urllib.request.urlopen(put_req, timeout=_HTTP_GET_TIMEOUT_S)
            return {"status": "toggled", "service": service_id, "list_id": list_id, "enabled": enabled}
        except Exception as exc:
            return {"error": str(exc)[:120]}

    # ensure_arr_scan_webhooks lives on ``_ContentAnalyticsMixin``.

    def delete_import_list(self, service_id: str, list_id: int) -> dict[str, Any]:
        """Delete an import list from a specific arr service."""
        api_keys = discover_api_keys()
        svc = SERVICE_MAP.get(service_id)
        if not svc or not svc.import_list_path:
            return {"error": f"Service '{service_id}' not found or has no import list support"}
        key = api_keys.get(service_id, "")
        if not key:
            return {"error": f"No API key for {service_id}"}
        try:
            req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}{svc.import_list_path}/{list_id}",
                method="DELETE", headers={"X-Api-Key": key},
            )
            urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S)
            return {"status": "deleted", "service": service_id, "list_id": list_id}
        except Exception as exc:
            return {"error": str(exc)[:120]}


    # get_download_client_settings / update_download_client_settings live
    # on ``_ContentDownloadSettingsMixin`` — see the mixin for their bodies.


def _fetch_qbit_downloads(svc_host: str, svc_port: int) -> dict[str, Any]:
    """Fetch active torrents from the torrent client API.

    Module-level — the prior ``@staticmethod`` lived on ``ContentService``
    but never touched ``self``, and keeping it here trims the class line
    count below the god-class threshold.
    """
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
    pw = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
    login = urllib.request.Request(
        f"http://{svc_host}:{svc_port}/api/v2/auth/login",
        data=f"username={user}&password={pw}".encode(),
    )
    opener.open(login, timeout=_HTTP_PROBE_TIMEOUT_S)
    req = urllib.request.Request(f"http://{svc_host}:{svc_port}/api/v2/torrents/info?filter=active")
    with opener.open(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
        torrents = json.loads(resp.read())
    items = [
        {"name": t.get("name", "")[:80],
         "progress": round((t.get("progress", 0) or 0) * 100, 1),
         "state": t.get("state", ""), "size": t.get("size", 0),
         "dlspeed": t.get("dlspeed", 0)}
        for t in torrents[:10]
    ]
    return {"active": len(torrents), "items": items}


def _fetch_sab_downloads(svc_host: str, svc_port: int) -> dict[str, Any]:
    """Fetch active NZB downloads from a usenet-client-compatible API.

    Extracted from ``ContentService`` for the same reason as
    ``_fetch_qbit_downloads`` — pure function, no ``self``, keeps the
    host class short enough to clear the 500-line ratchet."""
    from .registry import read_api_key_from_file
    _usenet_ids = [sid for sid, cat in DOWNLOAD_CLIENT_CATEGORIES.items() if cat == "usenet"]
    _usenet_svc_id = _usenet_ids[0] if _usenet_ids else "usenet"
    _usenet_svc = SERVICE_MAP.get(_usenet_svc_id)
    _key_env = _usenet_svc.api_key_env if _usenet_svc else ""
    sab_key = os.environ.get(_key_env, "") if _key_env else ""
    if not sab_key:
        sab_key = read_api_key_from_file(_usenet_svc_id, os.environ.get("CONFIG_ROOT", "/srv-config"))
    if not sab_key:
        return {"active": 0, "speed": "0", "items": []}
    req = urllib.request.Request(
        f"http://{svc_host}:{svc_port}/api?mode=queue&output=json&apikey={sab_key}"
    )
    with urllib.request.urlopen(req, timeout=_HTTP_PROBE_TIMEOUT_S) as resp:
        data = json.loads(resp.read())
    queue = data.get("queue", {})
    slots = queue.get("slots", [])
    items = [
        {"name": s.get("filename", "")[:80],
         "progress": round(float(s.get("percentage", 0)), 1)}
        for s in slots[:10]
    ]
    return {"active": len(slots), "speed": f"{queue.get('speed', '0')} KB/s", "items": items}


_instance = ContentService()

# Backward compat — callers use module-level functions
get_versions = _instance.get_versions
get_downloads = _instance.get_downloads
get_stats = _instance.get_stats
get_indexers = _instance.get_indexers
get_indexer_stats = _instance.get_indexer_stats
get_download_history = _instance.get_download_history
get_quality_profiles = _instance.get_quality_profiles
get_import_lists = _instance.get_import_lists
get_media_server_libraries = _instance.get_media_server_libraries
get_recent = _instance.get_recent
toggle_indexer = _instance.toggle_indexer
delete_indexer = _instance.delete_indexer
get_all_import_lists = _instance.get_all_import_lists
get_download_analytics = _instance.get_download_analytics
toggle_import_list = _instance.toggle_import_list
delete_import_list = _instance.delete_import_list

# Backward compat alias
get_jellyfin_libraries = get_media_server_libraries
# ``_fetch_qbit_downloads`` / ``_fetch_sab_downloads`` were promoted from
# staticmethods on ``ContentService`` to module-level helpers (see their
# definitions above); they are already in the module namespace.

# Download client category → fetch function.  Extend for new client types.
_DOWNLOAD_FETCHERS: dict[str, Any] = {
    "torrent": _fetch_qbit_downloads,
    "usenet": _fetch_sab_downloads,
}
# Map service IDs to their download category (from app layer).
_DOWNLOAD_CLIENT_IDS: dict[str, str] = DOWNLOAD_CLIENT_CATEGORIES
get_download_client_settings = _instance.get_download_client_settings
update_download_client_settings = _instance.update_download_client_settings
ensure_arr_scan_webhooks = _instance.ensure_arr_scan_webhooks
