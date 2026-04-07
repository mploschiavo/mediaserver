"""Health probe services: service reachability, auth validation, history."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SERVICE_PROBES: dict[str, tuple[str, int, str]] = {
    "jellyfin": ("jellyfin", 8096, "/System/Info/Public"),
    "jellyseerr": ("jellyseerr", 5055, "/api/v1/status"),
    "sonarr": ("sonarr", 8989, "/ping"),
    "radarr": ("radarr", 7878, "/ping"),
    "lidarr": ("lidarr", 8686, "/ping"),
    "readarr": ("readarr", 8787, "/ping"),
    "prowlarr": ("prowlarr", 9696, "/ping"),
    "qbittorrent": ("qbittorrent", 8080, "/"),
    "sabnzbd": ("sabnzbd", 8080, "/"),
    "bazarr": ("bazarr", 6767, "/"),
    "maintainerr": ("maintainerr", 6246, "/app/maintainerr/api/settings"),
    "tautulli": ("tautulli", 8181, "/status"),
    "homepage": ("homepage", 3000, "/"),
    "envoy": ("envoy", 9901, "/ready"),
    "plex": ("plex", 32400, "/identity"),
    "flaresolverr": ("flaresolverr", 8191, "/"),
}

AUTH_PROBES: dict[str, tuple[str, int, str, str]] = {
    "sonarr": ("sonarr", 8989, "/api/v3/system/status", "X-Api-Key"),
    "radarr": ("radarr", 7878, "/api/v3/system/status", "X-Api-Key"),
    "lidarr": ("lidarr", 8686, "/api/v1/system/status", "X-Api-Key"),
    "readarr": ("readarr", 8787, "/api/v1/system/status", "X-Api-Key"),
    "prowlarr": ("prowlarr", 9696, "/api/v1/system/status", "X-Api-Key"),
    "bazarr": ("bazarr", 6767, "/api/system/status", "X-Api-Key"),
    "jellyfin": ("jellyfin", 8096, "/System/Info", "X-Emby-Token"),
    "jellyseerr": ("jellyseerr", 5055, "/api/v1/settings/main", "X-Api-Key"),
    "sabnzbd": ("sabnzbd", 8080, "/api", "query:apikey"),
    "tautulli": ("tautulli", 8181, "/api/v2", "query:apikey"),
}

_HEALTH_HISTORY_PATH = Path(os.environ.get("HEALTH_HISTORY_PATH", "/tmp/media-stack-health-history.json"))
_HEALTH_HISTORY_LOCK = threading.Lock()


def discover_api_keys() -> dict[str, str]:
    """Read API keys — prefer env vars, fall back to config files."""
    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    keys: dict[str, str] = {}

    env_map = {
        "sonarr": "SONARR_API_KEY", "radarr": "RADARR_API_KEY",
        "lidarr": "LIDARR_API_KEY", "readarr": "READARR_API_KEY",
        "prowlarr": "PROWLARR_API_KEY", "bazarr": "BAZARR_API_KEY",
        "sabnzbd": "SABNZBD_API_KEY", "jellyseerr": "JELLYSEERR_API_KEY",
        "jellyfin": "JELLYFIN_API_KEY", "tautulli": "TAUTULLI_API_KEY",
    }
    for app, env_key in env_map.items():
        val = (os.environ.get(env_key) or "").strip()
        if val:
            keys[app] = val

    for app in ("sonarr", "radarr", "lidarr", "readarr", "prowlarr"):
        if app in keys:
            continue
        xml = config_root / app / "config.xml"
        if xml.exists():
            text = xml.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
            if m:
                keys[app] = m.group(1).strip()

    if "sabnzbd" not in keys:
        sab_ini = config_root / "sabnzbd" / "sabnzbd.ini"
        if sab_ini.exists():
            text = sab_ini.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^\s*api_key\s*=\s*(\S+)", text, re.MULTILINE)
            if m:
                keys["sabnzbd"] = m.group(1).strip()

    if "bazarr" not in keys:
        from media_stack.api.preflight.api_keys import _read_bazarr_api_key
        bazarr_cfg = config_root / "bazarr" / "config" / "config.yaml"
        bazarr_key = _read_bazarr_api_key(bazarr_cfg)
        if bazarr_key:
            keys["bazarr"] = bazarr_key

    if "jellyseerr" not in keys:
        js_settings = config_root / "jellyseerr" / "settings.json"
        if js_settings.exists():
            try:
                data = json.loads(js_settings.read_text(encoding="utf-8", errors="replace"))
                api_key = str((data.get("main") or {}).get("apiKey", "")).strip()
                if api_key:
                    keys["jellyseerr"] = api_key
            except Exception:
                pass

    if "jellyfin" not in keys:
        jf_db = config_root / "jellyfin" / "data" / "jellyfin.db"
        if jf_db.exists():
            try:
                conn = sqlite3.connect(f"file:{jf_db}?mode=ro", uri=True)
                cur = conn.cursor()
                cur.execute("SELECT AccessToken FROM ApiKeys ORDER BY Id DESC LIMIT 1")
                row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    keys["jellyfin"] = str(row[0]).strip()
            except Exception:
                pass

    if "tautulli" not in keys:
        tautulli_ini = config_root / "tautulli" / "config.ini"
        if tautulli_ini.exists():
            text = tautulli_ini.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^\s*api_key\s*=\s*(\S+)", text, re.MULTILINE)
            if m:
                keys["tautulli"] = m.group(1).strip()

    return keys


def probe_services(cache: Any) -> dict[str, Any]:
    """Probe all services: reachability + authenticated API validation."""
    cached = cache.get("health", 10)
    if cached is not None:
        return cached
    from concurrent.futures import ThreadPoolExecutor, as_completed

    api_keys = discover_api_keys()

    def probe(name: str) -> tuple[str, dict[str, Any]]:
        host, port, path = SERVICE_PROBES[name]
        result: dict[str, Any] = {"status": "unknown"}
        t0 = time.time()
        try:
            url = f"http://{host}:{port}{path}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                result["status"] = "ok"
                result["code"] = resp.status
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                result["status"] = "ok"
                result["code"] = exc.code
            else:
                result["status"] = "error"
                result["code"] = exc.code
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)[:80]
        result["ms"] = round((time.time() - t0) * 1000)

        key = api_keys.get(name)
        if key and name in AUTH_PROBES:
            a_host, a_port, a_path, a_mode = AUTH_PROBES[name]
            if a_mode.startswith("query:"):
                param = a_mode.split(":", 1)[1]
                a_url = f"http://{a_host}:{a_port}{a_path}?{param}={key}&output=json&mode=version"
                headers: dict[str, str] = {}
            else:
                a_url = f"http://{a_host}:{a_port}{a_path}"
                headers = {a_mode: key}
            try:
                req = urllib.request.Request(a_url, method="GET", headers=headers)
                with urllib.request.urlopen(req, timeout=4) as resp:
                    result["auth"] = "ok"
            except urllib.error.HTTPError as exc:
                result["auth"] = "unauthorized" if exc.code in (401, 403) else "error"
            except Exception:
                result["auth"] = "error"
        elif name in AUTH_PROBES:
            result["auth"] = "no_key"
        else:
            result["auth"] = "n/a"

        return name, result

    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(probe, name): name for name in SERVICE_PROBES}
        for future in as_completed(futures):
            try:
                name, result = future.result()
                results[name] = result
            except Exception:
                pass

    healthy = sum(1 for v in results.values() if v.get("status") == "ok")
    total = len(results)
    response = {"services": results, "healthy": healthy, "total": total}
    cache.set("health", response)
    return response


def append_health_history(services: dict[str, Any]) -> None:
    """Append a health probe result to persistent history for SLA."""
    entry = {
        "ts": time.time(),
        "services": {
            name: {"status": v.get("status", "unknown"), "ms": v.get("ms")}
            for name, v in services.items()
        },
    }
    with _HEALTH_HISTORY_LOCK:
        history: list[dict[str, Any]] = []
        if _HEALTH_HISTORY_PATH.exists():
            try:
                history = json.loads(_HEALTH_HISTORY_PATH.read_text())
            except Exception:
                pass
        history.append(entry)
        history = history[-1440:]  # Keep ~24h at 1-min intervals
        try:
            _HEALTH_HISTORY_PATH.write_text(json.dumps(history))
        except Exception:
            pass


def get_health_history() -> dict[str, Any]:
    """Return health history for SLA calculations."""
    with _HEALTH_HISTORY_LOCK:
        if not _HEALTH_HISTORY_PATH.exists():
            return {"history": [], "period_hours": 0}
        try:
            history = json.loads(_HEALTH_HISTORY_PATH.read_text())
        except Exception:
            return {"history": [], "period_hours": 0}
    if not history:
        return {"history": [], "period_hours": 0}
    first_ts = history[0].get("ts", time.time())
    period_hours = round((time.time() - first_ts) / 3600, 1)
    sla: dict[str, dict[str, Any]] = {}
    for entry in history:
        for name, info in entry.get("services", {}).items():
            if name not in sla:
                sla[name] = {"total": 0, "ok": 0}
            sla[name]["total"] += 1
            if info.get("status") == "ok":
                sla[name]["ok"] += 1
    for name in sla:
        t = sla[name]["total"]
        sla[name]["uptime_pct"] = round(sla[name]["ok"] / t * 100, 2) if t else 0
    return {"sla": sla, "period_hours": period_hours, "entries": len(history)}
