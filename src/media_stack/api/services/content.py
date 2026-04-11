"""Content services: library stats, downloads, indexers, versions, history."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from media_stack.services.apps.download_clients.registry_helpers import DOWNLOAD_CLIENT_CATEGORIES
from .health import discover_api_keys
from .registry import SERVICE_MAP, SERVICES


def get_versions(cache: Any) -> dict[str, Any]:
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
        headers: dict[str, str] = {"Accept": "application/json"}
        if key:
            headers["X-Api-Key"] = key
        try:
            req = urllib.request.Request(f"http://{host}:{port}{path}", headers=headers)
            with urllib.request.urlopen(req, timeout=4) as resp:
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
            except Exception:
                pass
    result = {"versions": versions}
    cache.set("versions", result)
    return result


def _fetch_qbit_downloads(svc_host: str, svc_port: int) -> dict[str, Any]:
    """Fetch active torrents from the torrent client API."""
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
    pw = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
    login = urllib.request.Request(
        f"http://{svc_host}:{svc_port}/api/v2/auth/login",
        data=f"username={user}&password={pw}".encode(),
    )
    opener.open(login, timeout=5)
    req = urllib.request.Request(f"http://{svc_host}:{svc_port}/api/v2/torrents/info?filter=active")
    with opener.open(req, timeout=5) as resp:
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
    """Fetch active NZB downloads from a usenet-client-compatible API."""
    from pathlib import Path
    from .registry import read_api_key_from_file
    # Discover the usenet client service ID from the download client registry.
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
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    queue = data.get("queue", {})
    slots = queue.get("slots", [])
    items = [
        {"name": s.get("filename", "")[:80],
         "progress": round(float(s.get("percentage", 0)), 1)}
        for s in slots[:10]
    ]
    return {"active": len(slots), "speed": f"{queue.get('speed', '0')} KB/s", "items": items}


# Download client category → fetch function.  Extend for new client types.
_DOWNLOAD_FETCHERS: dict[str, Any] = {
    "torrent": _fetch_qbit_downloads,
    "usenet": _fetch_sab_downloads,
}

# Map service IDs to their download category (from app layer).
_DOWNLOAD_CLIENT_IDS: dict[str, str] = DOWNLOAD_CLIENT_CATEGORIES


def get_downloads() -> dict[str, Any]:
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


def get_stats(cache: Any) -> dict[str, Any]:
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
            with urllib.request.urlopen(req, timeout=5) as resp:
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
            except Exception:
                pass
    result = {"stats": stats}
    cache.set("stats", result)
    return result


def get_indexers() -> dict[str, Any]:
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
        with urllib.request.urlopen(req, timeout=5) as resp:
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


def get_indexer_stats() -> dict[str, Any]:
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
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        stats = data.get("indexers", data) if isinstance(data, dict) else data
        return {"stats": stats if isinstance(stats, list) else []}
    except Exception:
        return {"stats": []}


def get_download_history() -> dict[str, Any]:
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
            with urllib.request.urlopen(req, timeout=5) as resp:
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
            except Exception:
                pass
    return {"history": history}


def get_quality_profiles() -> dict[str, Any]:
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
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            result = []
            if isinstance(data, list):
                for p in data:
                    entry: dict[str, Any] = {"id": p.get("id"), "name": p.get("name", "")}
                    if "upgradeAllowed" in p:
                        entry["upgradeAllowed"] = p["upgradeAllowed"]
                    cutoff_id = p.get("cutoff")
                    if cutoff_id is not None:
                        cutoff_name = str(cutoff_id)
                        for item in p.get("items", []):
                            if item.get("id") == cutoff_id:
                                cutoff_name = item.get("name", cutoff_name)
                                break
                            found = False
                            for q in item.get("items", []):
                                if q.get("id") == cutoff_id:
                                    cutoff_name = q.get("name", cutoff_name)
                                    found = True
                                    break
                            if found:
                                break
                        entry["cutoff"] = cutoff_name
                    result.append(entry)
            return name, result
        except Exception:
            return name, []

    profiles: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for f in as_completed([pool.submit(fetch, a[0]) for a in apps]):
            try:
                n, v = f.result()
                profiles[n] = v
            except Exception:
                pass
    return {"profiles": profiles}


def get_import_lists() -> dict[str, Any]:
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
            with urllib.request.urlopen(req, timeout=5) as resp:
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
            except Exception:
                pass
    return {"lists": lists}


def get_media_server_libraries() -> dict[str, Any]:
    """Fetch library list from the active media server (registry-driven)."""
    # Find the media server service that has a host/port configured
    for svc in SERVICES:
        if svc.category != "media-server" or not svc.host or not svc.port:
            continue
        env_key = svc.api_key_env or f"{svc.id.upper()}_API_KEY"
        key = os.environ.get(env_key, "")
        if not key:
            continue
        try:
            # Emby/Jellyfin use X-Emby-Token, Plex uses X-Plex-Token
            auth_header = "X-Emby-Token" if svc.auth_mode == "X-Emby-Token" else svc.auth_mode
            req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}/Library/VirtualFolders",
                headers={auth_header: key},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            libs = [
                {"name": lib.get("Name", ""), "type": lib.get("CollectionType", ""),
                 "paths": lib.get("Locations", []), "count": lib.get("ItemCount", 0)}
                for lib in (data if isinstance(data, list) else [])
            ]
            return {"libraries": libs}
        except Exception:
            return {"libraries": []}
    return {"libraries": []}


# Backward compat alias
get_jellyfin_libraries = get_media_server_libraries


def get_recent() -> dict[str, Any]:
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
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            items = data[:5] if isinstance(data, list) else []
            return name, [
                {"title": i.get("title", ""), "added": str(i.get("dateAdded", ""))[:10]}
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
            except Exception:
                pass
    return {"recent": recent}


# ---------------------------------------------------------------------------
# Indexer management — enable/disable, manual add (via Prowlarr API)
# ---------------------------------------------------------------------------

def toggle_indexer(indexer_id: int, enable: bool) -> dict[str, Any]:
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
        with urllib.request.urlopen(req, timeout=5) as resp:
            indexer = json.loads(resp.read())
        indexer["enable"] = enable
        # PUT updated config
        put_req = urllib.request.Request(
            f"http://{svc.host}:{svc.port}{svc.indexer_path}/{indexer_id}",
            data=json.dumps(indexer).encode(), method="PUT",
            headers={"X-Api-Key": key, "Content-Type": "application/json"},
        )
        urllib.request.urlopen(put_req, timeout=5)
        return {"status": "ok", "indexer_id": indexer_id, "enable": enable}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def delete_indexer(indexer_id: int) -> dict[str, Any]:
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
        urllib.request.urlopen(req, timeout=5)
        return {"status": "deleted", "indexer_id": indexer_id}
    except Exception as exc:
        return {"error": str(exc)[:120]}


# ---------------------------------------------------------------------------
# Import list management — add/remove Trakt/IMDb/RSS lists (via Arr APIs)
# ---------------------------------------------------------------------------

def get_all_import_lists() -> dict[str, Any]:
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
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            all_lists[svc_id] = [
                {"id": l.get("id"), "name": l.get("name", ""), "listType": l.get("listType", ""),
                 "enabled": l.get("enableAuto", True)}
                for l in (data if isinstance(data, list) else [])
            ]
        except Exception:
            all_lists[svc_id] = []
    return {"lists": all_lists, "total": sum(len(v) for v in all_lists.values())}


def get_download_analytics() -> dict[str, Any]:
    """Aggregate download history into analytics: counts by day, success rates, top indexers."""
    api_keys = discover_api_keys()
    apps = [(s.id, s.host, s.port, s.history_path) for s in SERVICES if s.history_path]
    all_records: list[dict[str, Any]] = []
    for svc_id, host, port, path in apps:
        key = api_keys.get(svc_id, "")
        if not key:
            continue
        try:
            # Fetch last 100 history records
            url = f"http://{host}:{port}{path}"
            if "?" in path:
                url += "&pageSize=100"
            else:
                url += "?pageSize=100"
            req = urllib.request.Request(url, headers={"X-Api-Key": key})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            records = data.get("records", data) if isinstance(data, dict) else data
            if isinstance(records, list):
                for r in records:
                    all_records.append({
                        "service": svc_id,
                        "title": str(r.get("sourceTitle", ""))[:60],
                        "event": str(r.get("eventType", "")),
                        "date": str(r.get("date", ""))[:10],
                        "quality": str(r.get("quality", {}).get("quality", {}).get("name", "")) if isinstance(r.get("quality"), dict) else "",
                        "indexer": str(r.get("data", {}).get("indexer", "")) if isinstance(r.get("data"), dict) else "",
                    })
        except Exception:
            pass

    # Aggregate by day
    by_day: dict[str, int] = {}
    by_service: dict[str, int] = {}
    by_indexer: dict[str, int] = {}
    for r in all_records:
        day = r.get("date", "unknown")
        by_day[day] = by_day.get(day, 0) + 1
        svc = r.get("service", "?")
        by_service[svc] = by_service.get(svc, 0) + 1
        idx = r.get("indexer", "")
        if idx:
            by_indexer[idx] = by_indexer.get(idx, 0) + 1

    # Sort by day descending
    daily_trend = [{"date": d, "count": c} for d, c in sorted(by_day.items(), reverse=True)][:30]
    top_indexers = sorted(by_indexer.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_records": len(all_records),
        "daily_trend": daily_trend,
        "by_service": by_service,
        "top_indexers": [{"name": n, "count": c} for n, c in top_indexers],
    }


def delete_import_list(service_id: str, list_id: int) -> dict[str, Any]:
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
        urllib.request.urlopen(req, timeout=5)
        return {"status": "deleted", "service": service_id, "list_id": list_id}
    except Exception as exc:
        return {"error": str(exc)[:120]}
