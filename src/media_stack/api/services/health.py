"""Health probe services: service reachability, auth validation, history."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import base64
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Any

logger = logging.getLogger("controller_api")

from .registry import SERVICES, read_api_key_from_file

# Build probe dicts from the service registry — no hardcoded service details here
SERVICE_PROBES: dict[str, tuple[str, int, str]] = {
    s.id: (s.host, s.port, s.health_path) for s in SERVICES
    if s.port > 0 and s.health_path  # Skip services without HTTP endpoints
}

AUTH_PROBES: dict[str, tuple[str, int, str, str]] = {
    s.id: (s.host, s.port, s.auth_path, s.auth_mode)
    for s in SERVICES if s.auth_path
}

# Login probes — test admin username/password per service
LOGIN_PROBES: dict[str, tuple[str, int, str, str]] = {
    s.id: (s.host, s.port, s.login_path, s.login_mode)
    for s in SERVICES if s.login_mode and s.login_path
}

_HEALTH_HISTORY_PATH = Path(os.environ.get("HEALTH_HISTORY_PATH", "/tmp/media-stack-health-history.json"))
_HEALTH_HISTORY_LOCK = threading.Lock()
_HEALTH_HISTORY_BUFFER: list[dict[str, Any]] = []
_HEALTH_HISTORY_LAST_FLUSH: float = 0.0
_HEALTH_HISTORY_FLUSH_INTERVAL: float = 30.0  # seconds between disk writes
_HEALTH_HISTORY_FLUSH_SIZE: int = 5  # entries before forced flush






class HealthService:
    """Service health probes, credential validation, and history tracking."""

    def probe_credentials(
        self,
        services: list[str] | None = None,
    ) -> dict[str, Any]:
        """Probe admin credentials for specified services (or all login-capable services).

        Used by the ad-hoc revalidation API endpoint.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
        all_keys = self.discover_api_keys()
        targets = LOGIN_PROBES
        if services:
            targets = {k: v for k, v in LOGIN_PROBES.items() if k in services}

        def _check(name: str) -> tuple[str, str]:
            host, port, path, mode = targets[name]
            svc_key = all_keys.get(name, "")
            logger.debug("[DEBUG] Credential probe: svc=%s, host=%s:%d, path=%s, mode=%s, "
                         "user=%s, has_api_key=%s", name, host, port, path, mode,
                         admin_user, bool(svc_key))
            result = _probe_login(host, port, path, mode, admin_user, admin_pass, api_key=svc_key)
            logger.debug("[DEBUG] Credential probe result: svc=%s → %s", name, result)
            return name, result

        results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_check, name): name for name in targets}
            for future in as_completed(futures):
                try:
                    name, status = future.result()
                    results[name] = status
                except Exception:
                    results[futures[future]] = "error"

        ok_count = sum(1 for v in results.values() if v == "ok")
        return {"credentials": results, "ok": ok_count, "total": len(results)}

    def discover_api_keys(self) -> dict[str, str]:
        """Read API keys — prefer env vars, fall back to config files."""
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        keys: dict[str, str] = {}

        # Built from registry — add a service to registry.py and it auto-discovers
        env_map = {s.id: s.api_key_env for s in SERVICES if s.api_key_env}
        for app, env_key in env_map.items():
            val = (os.environ.get(env_key) or "").strip()
            if val:
                keys[app] = val

        # Fall back to config files — driven entirely by the registry
        for svc in SERVICES:
            if not svc.api_key_env or svc.id in keys:
                continue
            key = read_api_key_from_file(svc.id, str(config_root))
            if key:
                keys[svc.id] = key

        return keys

    @staticmethod
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
            except Exception as exc:
                log_swallowed(exc)
        else:
            try:
                import docker
                client = docker.from_env()
                for c in client.containers.list():
                    names.add(c.name)
            except Exception as exc:
                log_swallowed(exc)
        return names

    def probe_services(self, cache: Any) -> dict[str, Any]:
        """Probe all services: reachability + authenticated API validation."""
        cached = cache.get("health", 10)
        if cached is not None:
            return cached
        from concurrent.futures import ThreadPoolExecutor, as_completed

        api_keys = self.discover_api_keys()
        running = self._get_running_containers()
        admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")

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

            # Login (credential) probe — test admin username/password
            if name in LOGIN_PROBES:
                l_host, l_port, l_path, l_mode = LOGIN_PROBES[name]
                svc_key = api_keys.get(name, "")
                result["login"] = _probe_login(l_host, l_port, l_path, l_mode, admin_user, admin_pass, api_key=svc_key)
            else:
                result["login"] = "n/a"

            return name, result

        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(probe, name): name for name in SERVICE_PROBES}
            for future in as_completed(futures):
                try:
                    name, result = future.result()
                    results[name] = result
                except Exception as exc:
                    log_swallowed(exc)

        # Mark services that have no HTTP endpoint (port=0) as disabled so
        # the dashboard doesn't show them as "pending".
        for svc in SERVICES:
            if svc.id not in results:
                results[svc.id] = {"status": "disabled", "auth": "n/a", "ms": 0}
        # Synthesize a "controller" entry the /api/services endpoint
        # injects but SERVICES doesn't know about. Without this the
        # dashboard's services table shows the Media Stack Controller
        # row as "Pending" forever — there's never a probe result for
        # it because it isn't in the registry. We're the code serving
        # this request, so by definition the controller is "ok".
        if "controller" not in results:
            results["controller"] = {
                "status": "ok", "auth": "n/a", "ms": 0,
                "code": HTTPStatus.OK,
            }

        healthy = sum(1 for v in results.values() if v.get("status") == "ok")
        total = len(results)
        response = {"services": results, "healthy": healthy, "total": total}
        cache.set("health", response)
        return response

    def append_health_history(self, services: dict[str, Any]) -> None:
        """Append a health probe result to an in-memory buffer.

        Flushes to disk when the buffer reaches *_FLUSH_SIZE* entries or
        *_FLUSH_INTERVAL* seconds have elapsed — avoids a full JSON
        read/write on every health check (was the single largest I/O cost).
        """
        global _HEALTH_HISTORY_LAST_FLUSH
        entry = {
            "ts": time.time(),
            "services": {
                name: {"status": v.get("status", "unknown"), "ms": v.get("ms")}
                for name, v in services.items()
            },
        }
        with _HEALTH_HISTORY_LOCK:
            _HEALTH_HISTORY_BUFFER.append(entry)
            now = time.time()
            should_flush = (
                len(_HEALTH_HISTORY_BUFFER) >= _HEALTH_HISTORY_FLUSH_SIZE
                or (now - _HEALTH_HISTORY_LAST_FLUSH) >= _HEALTH_HISTORY_FLUSH_INTERVAL
            )
            if should_flush:
                _flush_health_history()
                _HEALTH_HISTORY_LAST_FLUSH = now

    def get_health_history(self) -> dict[str, Any]:
        """Return health history for SLA calculations."""
        with _HEALTH_HISTORY_LOCK:
            if not _HEALTH_HISTORY_PATH.exists():
                return {"history": [], "period_hours": 0}
            try:
                history = json.loads(_HEALTH_HISTORY_PATH.read_text(encoding="utf-8"))
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


    @staticmethod
    def _probe_login(
        host: str, port: int, path: str, mode: str, username: str, password: str,
        api_key: str = "",
    ) -> str:
        """Test admin credential login for a single service. Returns status string.
    
        Returns "disabled" if the service does not require authentication
        (e.g. AuthenticationMethod=None in Arr apps, or DisabledForLocalAddresses
        when the controller is on a local subnet).
        """
        # Pre-check: detect if the service has authentication disabled.
        # Basic mode: unauthenticated GET returns 200 → disabled, 401/403 → required.
        # Form mode (Arr apps): query /api/v{1,3}/system/status with API key for the
        # "authentication" field — "none" means auth is off.
        if mode == "basic":
            try:
                check_req = urllib.request.Request(f"http://{host}:{port}{path}", method="GET")
                with urllib.request.urlopen(check_req, timeout=3) as resp:
                    if resp.status == 200:
                        return "disabled"
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    pass  # Auth required — proceed
            except Exception as exc:
                log_swallowed(exc)
        elif mode == "form" and api_key:
            # Arr apps expose authentication mode at /api/v{1,3}/system/status
            for api_ver in ("v3", "v1"):
                try:
                    status_url = f"http://{host}:{port}/api/{api_ver}/system/status"
                    req = urllib.request.Request(status_url, method="GET",
                                                headers={"X-Api-Key": api_key})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                        auth_mode = str(data.get("authentication", "")).lower()
                        if auth_mode in ("none", ""):
                            return "disabled"
                        break  # Got a valid response — stop trying API versions
                except Exception as exc:
                    log_swallowed(exc)
                    continue
    
        try:
            if mode == "json_credentials":
                url = f"http://{host}:{port}{path}"
                payload = json.dumps({"Username": username, "Pw": password}).encode()
                headers = {
                    "Content-Type": "application/json",
                    "X-Emby-Authorization": (
                        'MediaBrowser Client="media-stack-controller", '
                        'Device="health-probe", DeviceId="health-probe", Version="1.0"'
                    ),
                }
                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    body = json.loads(resp.read().decode("utf-8", errors="replace"))
                    if body.get("AccessToken"):
                        return "ok"
                return "fail"
    
            elif mode == "basic":
                url = f"http://{host}:{port}{path}"
                cred = base64.b64encode(f"{username}:{password}".encode()).decode()
                req = urllib.request.Request(url, headers={"Authorization": f"Basic {cred}"}, method="GET")
                with urllib.request.urlopen(req, timeout=5):
                    return "ok"
    
            elif mode == "form":
                url = f"http://{host}:{port}{path}"
                data = urllib.parse.urlencode({"username": username, "password": password}).encode()
                req = urllib.request.Request(url, data=data, method="POST")
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    final_url = resp.url if hasattr(resp, "url") else ""
                    # qBittorrent: "Ok." on success, "Fails." on bad creds
                    if "Fails" in body:
                        return "fail"
                    # Arr apps: redirect to /login?loginFailed=true on failure
                    if "loginFailed=true" in final_url:
                        return "fail"
                    return "ok"
    
            return "n/a"
        except urllib.error.HTTPError as exc:
            return "fail" if exc.code in (400, 401, 403) else "error"
        except urllib.error.URLError:
            return "error"
        except (TimeoutError, OSError) as exc:
            logger.debug("Login probe %s:%d failed: %s", host, port, exc)
            return "error"
        except Exception as exc:
            logger.debug("Login probe %s:%d unexpected error: %s", host, port, exc)
            return "error"

    @staticmethod
    def _flush_health_history() -> None:
        """Write buffered entries to disk. Must be called under _HEALTH_HISTORY_LOCK."""
        if not _HEALTH_HISTORY_BUFFER:
            return
        history: list[dict[str, Any]] = []
        if _HEALTH_HISTORY_PATH.exists():
            try:
                history = json.loads(_HEALTH_HISTORY_PATH.read_text(encoding="utf-8"))
            except Exception as exc:
                log_swallowed(exc)
        history.extend(_HEALTH_HISTORY_BUFFER)
        _HEALTH_HISTORY_BUFFER.clear()
        history = history[-1440:]  # Keep ~24h at 1-min intervals
        try:
            _HEALTH_HISTORY_PATH.write_text(json.dumps(history), encoding="utf-8")
        except Exception as exc:
            log_swallowed(exc)


_instance = HealthService()

# Backward compat — callers use module-level functions
probe_credentials = _instance.probe_credentials
discover_api_keys = _instance.discover_api_keys
_get_running_containers = _instance._get_running_containers
probe_services = _instance.probe_services
append_health_history = _instance.append_health_history
get_health_history = _instance.get_health_history
_probe_login = _instance._probe_login
_flush_health_history = _instance._flush_health_history
