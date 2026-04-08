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

from .health import discover_api_keys
from .registry import SERVICES


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


def get_downloads() -> dict[str, Any]:
    """Fetch active downloads from qBittorrent and SABnzbd."""
    result: dict[str, Any] = {}

    # qBittorrent
    try:
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        pw = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
        login = urllib.request.Request(
            "http://qbittorrent:8080/api/v2/auth/login",
            data=f"username={user}&password={pw}".encode(),
        )
        opener.open(login, timeout=5)
        req = urllib.request.Request("http://qbittorrent:8080/api/v2/torrents/info?filter=active")
        with opener.open(req, timeout=5) as resp:
            torrents = json.loads(resp.read())
        items = []
        for t in torrents[:10]:
            items.append({
                "name": t.get("name", "")[:80],
                "progress": round((t.get("progress", 0) or 0) * 100, 1),
                "state": t.get("state", ""),
                "size": t.get("size", 0),
                "dlspeed": t.get("dlspeed", 0),
            })
        result["qbittorrent"] = {"active": len(torrents), "items": items}
    except Exception as exc:
        result["qbittorrent"] = {"active": 0, "items": [], "error": str(exc)[:60]}

    # SABnzbd
    try:
        sab_key = os.environ.get("SABNZBD_API_KEY", "")
        if not sab_key:
            from pathlib import Path
            sab_ini = Path(os.environ.get("CONFIG_ROOT", "/srv-config")) / "sabnzbd" / "sabnzbd.ini"
            if sab_ini.exists():
                m = re.search(r"^\s*api_key\s*=\s*(\S+)", sab_ini.read_text(), re.MULTILINE)
                if m:
                    sab_key = m.group(1)
        if sab_key:
            req = urllib.request.Request(
                f"http://sabnzbd:8080/api?mode=queue&output=json&apikey={sab_key}"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            queue = data.get("queue", {})
            slots = queue.get("slots", [])
            items = []
            for s in slots[:10]:
                items.append({
                    "name": s.get("filename", "")[:80],
                    "progress": round(float(s.get("percentage", 0)), 1),
                })
            speed = queue.get("speed", "0")
            result["sabnzbd"] = {"active": len(slots), "speed": f"{speed} KB/s", "items": items}
    except Exception as exc:
        result["sabnzbd"] = {"active": 0, "speed": "0", "items": [], "error": str(exc)[:60]}

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
    """Fetch indexer list from services with indexer_path (e.g. Prowlarr)."""
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
            return name, [{"id": p.get("id"), "name": p.get("name", "")} for p in data] if isinstance(data, list) else []
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


def get_jellyfin_libraries() -> dict[str, Any]:
    """Fetch Jellyfin library list."""
    key = os.environ.get("JELLYFIN_API_KEY", "")
    if not key:
        return {"libraries": []}
    try:
        req = urllib.request.Request(
            "http://jellyfin:8096/Library/VirtualFolders",
            headers={"X-Emby-Token": key},
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
