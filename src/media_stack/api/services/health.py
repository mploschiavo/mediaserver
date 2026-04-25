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


def _running_k8s_pod_names(namespace: str) -> set[str]:
    """Return the ``app``/``name`` labels of Running pods in *namespace*.

    Extracted from ``_get_running_containers`` to drop the enclosing
    ``if namespace:`` branch from 5 levels of nesting down to 2. The
    entire body is best-effort — any failure to talk to the API
    yields an empty set rather than propagating.
    """
    names: set[str] = set()
    try:
        from kubernetes import client as k8s_client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        v1 = k8s_client.CoreV1Api()
        pods = v1.list_namespaced_pod(namespace)
        for p in pods.items:
            if p.status.phase != "Running":
                continue
            labels = p.metadata.labels or {}
            names.add(labels.get("app", p.metadata.name))
    except Exception as exc:
        log_swallowed(exc)
    return names


def _running_compose_container_names() -> set[str]:
    """Return the names of locally running Docker Compose containers.

    Counterpart to ``_running_k8s_pod_names`` — kept as a module-level
    helper so the dispatcher stays readable.
    """
    names: set[str] = set()
    try:
        import docker
        client = docker.from_env()
        for c in client.containers.list():
            names.add(c.name)
    except Exception as exc:
        log_swallowed(exc)
    return names

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

# Captured once at import. The controller process start time feeds
# ``/api/ops/health.uptime_seconds``. Sampling per-call gives the
# user "how long has this controller pod been up" — exactly what
# the ops dashboard tile is asking. Container/pod restarts re-import
# this module, resetting the clock; that's the right semantic.
_PROCESS_START_TIME: float = time.time()

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
        """Probe that each service's connectivity credential is valid.

        **Split from password-propagation** (2026-04-24 incident): this
        method now validates the **API key** where a service exposes one
        (``auth_mode == "X-Emby-Token"`` or ``"X-Api-Key"``), via a
        **read-only** ``GET <auth_path>`` call. It no longer POSTs to
        ``/Users/AuthenticateByName`` — that endpoint has side effects
        (increments ``InvalidLoginAttemptCount`` on every miss, races on
        the user row) and was the source of a noisy Jellyfin 400 flood
        in production. See ``probe_password_propagation`` for the
        separate admin-supplied password check.

        Services without an API key (e.g. qBittorrent form auth) still
        fall through to ``_probe_login`` with the admin creds.

        Per-service status values:
          * ``ok``            — API key / login works.
          * ``fail``          — credential rejected.
          * ``error``         — transport / parsing failure.
          * ``disabled``      — service has auth turned off.
          * ``no_key``        — no API key discovered AND service
                                requires one; password path unavailable.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
        all_keys = self.discover_api_keys()
        targets = LOGIN_PROBES
        if services:
            targets = {k: v for k, v in LOGIN_PROBES.items() if k in services}

        # For per-service routing decisions we need the auth_path +
        # auth_mode (API key shape). Build a small side-index from
        # SERVICES so we don't change the LOGIN_PROBES tuple shape.
        svc_meta: dict[str, tuple[str, str]] = {
            s.id: (s.auth_path, s.auth_mode) for s in SERVICES
        }

        def _check(name: str) -> tuple[str, str]:
            host, port, path, mode = targets[name]
            svc_key = all_keys.get(name, "")
            auth_path, auth_mode = svc_meta.get(name, ("", ""))
            logger.debug(
                "[DEBUG] Credential probe: svc=%s, host=%s:%d, path=%s, "
                "mode=%s, auth_mode=%s, has_api_key=%s",
                name, host, port, path, mode, auth_mode, bool(svc_key),
            )
            # Prefer the API-key path when the service exposes a
            # token-based auth surface. Read-only GETs on
            # ``<auth_path>`` cannot mutate the user row, so no
            # concurrency races and no "failed login" noise in the
            # downstream service's log.
            if auth_mode in ("X-Emby-Token", "X-Api-Key"):
                if not svc_key:
                    return name, "no_key"
                result = self._probe_api_key_health(
                    host, port, auth_path or path, auth_mode, svc_key,
                )
                logger.debug(
                    "[DEBUG] API-key probe result: svc=%s → %s",
                    name, result,
                )
                return name, result
            # Fall through for services without a token-based API
            # (e.g. qBittorrent form-login is the last caller here).
            result = _probe_login(
                host, port, path, mode, admin_user, admin_pass,
                api_key=svc_key,
            )
            logger.debug(
                "[DEBUG] Login probe result: svc=%s → %s", name, result,
            )
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

    def probe_password_propagation(
        self,
        services: list[str] | None = None,
    ) -> dict[str, Any]:
        """Confirm the stack admin's password propagated to each
        service's **local** user record.

        Distinct from ``probe_credentials``:

        - ``probe_credentials`` asks *"does the API key work?"* — a
          connectivity check. No password involved.
        - ``probe_password_propagation`` asks *"did the stack-admin
          password reset reach this service's local user store?"* —
          a sync check. No authentication attempt; just a metadata
          read via the API key.

        For Jellyfin this GETs ``/Users/{user_id}`` and reads the
        ``HasPassword`` boolean. A ``true`` means a password is
        stored (i.e. the propagation step at the end of
        ``UserWriteService.reset_password`` succeeded). A ``false``
        means the user row exists but the password was never set —
        common when Jellyfin auto-provisioned the admin via OIDC
        first-login and the stack's local-password propagation was
        never run.

        Per-service status values:
          * ``ok``             — local password is set.
          * ``not_propagated`` — user exists but ``HasPassword=false``;
                                 run the admin-reset flow to fix.
          * ``no_user``        — admin user not found in the service.
          * ``no_key``         — can't even check; API key missing.
          * ``error``          — transport / parsing failure.
          * ``n/a``            — service doesn't have a per-user
                                 local-password concept (e.g. *arr
                                 apps use API key only).

        Admin-only. Read-only. No side effects.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        all_keys = self.discover_api_keys()
        targets = LOGIN_PROBES
        if services:
            targets = {k: v for k, v in LOGIN_PROBES.items() if k in services}

        # Only services with a local user-password store can be
        # checked meaningfully. Today that's Jellyfin. Jellyseerr uses
        # OIDC + JSON local accounts — its API exposes a similar
        # ``HasPassword`` per user, so it's added below as support.
        supported: dict[str, dict[str, str]] = {
            "jellyfin": {
                "user_list_path": "/Users",
                "has_password_key": "HasPassword",
                "auth_header": "X-Emby-Token",
            },
        }

        def _check(name: str) -> tuple[str, str]:
            meta = supported.get(name)
            if meta is None:
                return name, "n/a"
            host, port, *_ = targets[name]
            svc_key = all_keys.get(name, "")
            if not svc_key:
                return name, "no_key"
            return name, self._probe_has_password(
                host=host, port=port, api_key=svc_key, admin_user=admin_user,
                user_list_path=meta["user_list_path"],
                has_password_key=meta["has_password_key"],
                auth_header=meta["auth_header"],
            )

        results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_check, name): name for name in targets}
            for future in as_completed(futures):
                try:
                    name, status = future.result()
                    results[name] = status
                except Exception:
                    results[futures[future]] = "error"

        ok_count = sum(1 for v in results.values() if v == "ok")
        checked = sum(1 for v in results.values() if v not in ("n/a",))
        return {
            "password_propagation": results,
            "ok": ok_count,
            "checked": checked,
            "total": len(results),
        }

    @staticmethod
    def _probe_api_key_health(
        host: str, port: int, path: str, auth_mode: str, api_key: str,
    ) -> str:
        """GET the service's auth-check path with the token header.

        Returns ``"ok"`` on any 2xx, ``"fail"`` on 401/403, ``"error"``
        otherwise. No side effects on the target — purely a read.
        """
        url = f"http://{host}:{port}{path}"
        header_name = auth_mode  # "X-Emby-Token" / "X-Api-Key"
        req = urllib.request.Request(
            url, headers={header_name: api_key}, method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return "ok" if 200 <= resp.status < 300 else "fail"
        except urllib.error.HTTPError as exc:
            return "fail" if exc.code in (401, 403) else "error"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.debug("API-key probe %s:%d failed: %s", host, port, exc)
            return "error"
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "API-key probe %s:%d unexpected error: %s", host, port, exc,
            )
            return "error"

    @staticmethod
    def _probe_has_password(
        *,
        host: str, port: int, api_key: str, admin_user: str,
        user_list_path: str, has_password_key: str, auth_header: str,
    ) -> str:
        """Read the service's user list, find the admin row, return
        whether its ``HasPassword`` (or equivalent) field is true.

        GET-only; no writes, no authentication attempts.
        """
        url = f"http://{host}:{port}{user_list_path}"
        req = urllib.request.Request(
            url, headers={auth_header: api_key}, method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if not (200 <= resp.status < 300):
                    return "error"
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            return "error" if exc.code not in (401, 403) else "no_key"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.debug(
                "HasPassword probe %s:%d failed: %s", host, port, exc,
            )
            return "error"

        if not isinstance(body, list):
            return "error"
        target = (admin_user or "admin").strip().lower()
        for user in body:
            if not isinstance(user, dict):
                continue
            if str(user.get("Name", "")).strip().lower() != target:
                continue
            return "ok" if bool(user.get(has_password_key, False)) else "not_propagated"
        return "no_user"

    def discover_api_keys(self) -> dict[str, str]:
        """Read API keys.

        On-disk config.xml is the SOURCE OF TRUTH because the *arr
        rewrites it whenever the key rotates (restart cycles often
        regenerate keys; env vars baked at controller-start go stale
        immediately). Env vars are the BOOTSTRAP fallback for the
        first run before any *arr has written its file.

        Until v1.0.134 this was reversed (env first, file fallback),
        which left ``/api/stats`` returning HTTP 401 across every
        *arr after a Sonarr/Radarr DB regenerate — Library tile
        showed 0/0/0/0 even with content present.
        """
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        keys: dict[str, str] = {}

        # Source of truth: config files
        for svc in SERVICES:
            if not svc.api_key_env:
                continue
            key = read_api_key_from_file(svc.id, str(config_root))
            if key:
                keys[svc.id] = key

        # Bootstrap fallback: env vars for services whose config
        # file isn't readable yet (cold start, never-bootstrapped).
        # Routed through ``runtime_keys.read_service_api_key`` so the
        # 30s cache is shared across this and every endpoint-side caller —
        # avoids re-statting every config file under a hot dashboard
        # render.
        from .runtime_keys import read_service_api_key
        env_map = {s.id: s.api_key_env for s in SERVICES if s.api_key_env}
        for app, _env_key in env_map.items():
            if app in keys:
                continue
            val = read_service_api_key(app)
            if val:
                keys[app] = val

        # Jellyfin stores its API key in SQLite, not a config file —
        # the per-format readers above can't reach it. Without this,
        # the controller's webhook handler (POST /webhooks/arr) can't
        # trigger ``/Library/Refresh`` after a *arr import, so newly
        # imported content sits invisible until Jellyfin's library
        # monitor finds it via inotify (slower). (v1.0.144.)
        if "jellyfin" not in keys:
            try:
                from media_stack.services.apps.jellyfin.api_key_db import (
                    read_jellyfin_api_key_from_db as _read_jf_db,
                )
                jf_cfg = {
                    "api_key_db_path": "jellyfin/data/jellyfin.db",
                    "api_key_name_preference": [
                        "Jellyfin", "Jellyseerr", "media-stack-controller",
                    ],
                }
                token, _ = _read_jf_db(
                    str(config_root), jf_cfg,
                    coerce_list=lambda v: list(v) if isinstance(v, (list, tuple)) else [v],
                    resolve_path=lambda root, rel: Path(root) / rel,
                )
                if token:
                    keys["jellyfin"] = token
            except Exception as exc:
                log_swallowed(exc)

        return keys

    @staticmethod
    def _get_running_containers() -> set[str]:
        """Get names of running containers (compose) or pods (K8s).

        Dispatches to the appropriate helper by whether ``K8S_NAMESPACE``
        is set — flattened from a 5-deep ``if/try/try/for/if`` into two
        guard-style helpers so each branch reads linearly.
        """
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if namespace:
            return _running_k8s_pod_names(namespace)
        return _running_compose_container_names()

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
        """Return health history for the ops dashboard sparkline + SLA.

        Emits BOTH shapes the UI accepts:
          * ``history``: raw per-tick samples ``[{ts, services}, ...]``,
            preferred by the sparkline so it can plot ok/total over
            time. Earlier revisions only emitted ``sla`` (an aggregate),
            which made HealthHistorySparkline render
            "No history yet — controller hasn't recorded enough probe
             samples to plot" even when ``entries`` was non-zero.
          * ``sla``: per-service uptime percentage rolled up across the
            entire history window. Used by the sparkline as a fallback
            and by other consumers that need the aggregate."""
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
        return {
            "history": history,
            "sla": sla,
            "period_hours": period_hours,
            "entries": len(history),
        }

    def get_ops_health(self) -> dict[str, Any]:
        """Return aggregated runtime stats for the /ops dashboard tile.

        Replaces the UI-side ``Promise.resolve({uptime: 0, containers:
        0, last_bootstrap_at: new Date(0)})`` stub that produced the
        infamous "12/31/1969" bootstrap timestamp. Each field is
        derived from a cheap, already-cached source — no extra HTTP
        probes beyond what the dashboard does anyway.

        Fields:
          * ``uptime_seconds``: time since the controller process
            started. Resets on pod restart.
          * ``containers``: count of live media-stack containers.
            On K8s this is the Running pod count in our namespace;
            on compose it's the running container set the existing
            ``_get_running_containers`` helper already computes.
          * ``disk_used_pct``: highest used-percent across the
            volumes ``DiskService`` reports. Surfaces capacity
            pressure even if only one volume is full.
          * ``last_bootstrap_at``: ISO timestamp of the most
            recent run that touched the bootstrap pipeline (any
            entry with source=='bootstrap' OR a job key starting
            with 'bootstrap'). Empty string if no bootstrap run is
            recorded — the UI renders that as ``—``."""
        return {
            "uptime_seconds": int(time.time() - _PROCESS_START_TIME),
            "containers": len(self._get_running_containers()),
            "disk_used_pct": self._max_disk_pct(),
            "last_bootstrap_at": self._last_bootstrap_iso(),
        }

    @staticmethod
    def _max_disk_pct() -> float:
        """Largest ``percent_used`` across reported volumes. The
        dashboard tile shows a single number; pick the worst so a
        full /media volume doesn't get hidden behind a 2%-used /config."""
        try:
            from media_stack.api.services.disk import get_disk
            volumes = (get_disk().get("disk") or {}).values()
            pcts = [
                float(v.get("percent_used", 0))
                for v in volumes
                if isinstance(v, dict) and isinstance(v.get("percent_used"), (int, float))
            ]
            return max(pcts) if pcts else 0.0
        except Exception as exc:
            log_swallowed(exc)
            return 0.0

    @staticmethod
    def _last_bootstrap_iso() -> str:
        """Find the most recent bootstrap-flavored run in
        ``job-history.json`` and return its ts as an ISO string.

        ``source=='bootstrap'`` is the canonical marker, but older
        entries used job-id prefixes like ``bootstrap:configure-...``.
        Walk newest-first and return on the first match. Empty string
        when no bootstrap has ever run (fresh deploy)."""
        from datetime import datetime, timezone
        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        path = Path(config_root) / ".controller" / "job-history.json"
        try:
            if not path.is_file():
                return ""
            history = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log_swallowed(exc)
            return ""
        if not isinstance(history, list):
            return ""
        for entry in reversed(history):
            if not isinstance(entry, dict):
                continue
            source = str(entry.get("source") or "")
            jobs = entry.get("jobs") or {}
            is_bootstrap = (
                source == "bootstrap"
                or any(str(k).startswith("bootstrap") for k in jobs)
            )
            if not is_bootstrap:
                continue
            ts = entry.get("ts")
            if isinstance(ts, (int, float)) and ts > 0:
                return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        return ""


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
probe_password_propagation = _instance.probe_password_propagation
discover_api_keys = _instance.discover_api_keys
_get_running_containers = _instance._get_running_containers
probe_services = _instance.probe_services
append_health_history = _instance.append_health_history
get_health_history = _instance.get_health_history
get_ops_health = _instance.get_ops_health
_probe_login = _instance._probe_login
_flush_health_history = _instance._flush_health_history
