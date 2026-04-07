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

from .registry import SERVICES, SERVICE_MAP

# Build probe dicts from the service registry — no hardcoded service details here
SERVICE_PROBES: dict[str, tuple[str, int, str]] = {
    s.id: (s.host, s.port, s.health_path) for s in SERVICES
}

AUTH_PROBES: dict[str, tuple[str, int, str, str]] = {
    s.id: (s.host, s.port, s.auth_path, s.auth_mode)
    for s in SERVICES if s.auth_path
}

_HEALTH_HISTORY_PATH = Path(os.environ.get("HEALTH_HISTORY_PATH", "/tmp/media-stack-health-history.json"))
_HEALTH_HISTORY_LOCK = threading.Lock()


def discover_api_keys() -> dict[str, str]:
    """Read API keys — prefer env vars, fall back to config files."""
    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    keys: dict[str, str] = {}

    # Built from registry — add a service to registry.py and it auto-discovers
    env_map = {s.id: s.api_key_env for s in SERVICES if s.api_key_env}
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


def _get_running_containers() -> set[str]:
    """Get names of running containers (compose) or pods (K8s)."""
    namespace = os.environ.get("K8S_NAMESPACE", "")
    names: set[str] = set()
    if namespace:
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            v1 = k8s_client.CoreV1Api()
            pods = v1.list_namespaced_pod(namespace)
            for p in pods.items:
                if p.status.phase == "Running":
                    labels = p.metadata.labels or {}
                    names.add(labels.get("app", p.metadata.name))
        except Exception:
            pass
    else:
        try:
            import docker
            client = docker.from_env()
            for c in client.containers.list():
                names.add(c.name)
        except Exception:
            pass
    return names


def probe_services(cache: Any) -> dict[str, Any]:
    """Probe all services: reachability + authenticated API validation."""
    cached = cache.get("health", 10)
    if cached is not None:
        return cached
    from concurrent.futures import ThreadPoolExecutor, as_completed

    api_keys = discover_api_keys()
    running = _get_running_containers()

    def probe(name: str) -> tuple[str, dict[str, Any]]:
        # Skip services that aren't running (behind inactive profiles)
        if running and name not in running:
            return name, {"status": "disabled", "auth": "n/a", "ms": 0}
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
