"""Telemetry client — collects cluster metrics and pushes to central server.

Lightweight, resilient, configurable. Runs on a schedule (default: hourly).
Buffers locally if the central server is unreachable.

Configuration via environment:
  TELEMETRY_ENABLED=1
  TELEMETRY_ENDPOINT=https://telemetry.example.com/api/v1/telemetry
  TELEMETRY_API_KEY=your-api-key
  TELEMETRY_INTERVAL_SECONDS=3600  (hourly)
  TELEMETRY_CLUSTER_ID=auto-generated-uuid
  TELEMETRY_CLUSTER_NAME=my-media-stack
"""

from __future__ import annotations

import json
import os
import platform
import socket
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def _config_root() -> str:
    return os.environ.get("CONFIG_ROOT", "/srv-config")


def _cluster_id() -> str:
    """Persistent cluster ID — generated once, stored to disk."""
    explicit = os.environ.get("TELEMETRY_CLUSTER_ID", "").strip()
    if explicit:
        return explicit
    id_file = Path(_config_root()) / ".controller" / "cluster-id"
    if id_file.is_file():
        return id_file.read_text().strip()
    cid = str(uuid.uuid4())
    try:
        id_file.parent.mkdir(parents=True, exist_ok=True)
        id_file.write_text(cid)
    except Exception:
        pass
    return cid


def _cluster_name() -> str:
    return os.environ.get("TELEMETRY_CLUSTER_NAME",
                          os.environ.get("COMPOSE_PROJECT_NAME",
                                         socket.gethostname()))


def collect_metrics() -> dict[str, Any]:
    """Collect all cluster metrics. Safe — never raises."""
    metrics: dict[str, Any] = {
        "cluster_id": _cluster_id(),
        "cluster_name": _cluster_name(),
        "ts": time.time(),
        "controller": _collect_controller_info(),
        "services": _collect_service_health(),
        "jobs": _collect_job_metrics(),
        "media": _collect_media_metrics(),
    }
    return metrics


def _collect_controller_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "platform": os.environ.get("K8S_NAMESPACE", "compose"),
        "python": platform.python_version(),
    }
    try:
        version_file = Path("/opt/media-stack/VERSION")
        if version_file.is_file():
            info["version"] = version_file.read_text().strip()
    except Exception:
        pass
    # Uptime from /proc
    try:
        with open("/proc/uptime") as f:
            info["uptime_hours"] = round(float(f.read().split()[0]) / 3600, 1)
    except Exception:
        pass
    return info


def _collect_service_health() -> dict[str, Any]:
    try:
        from media_stack.api.services.registry import SERVICES
        from media_stack.api.services.health import probe_services
        from media_stack.api.cache import api_cache
        result = probe_services(api_cache)
        services = result.get("services", {})
        healthy = sum(1 for s in services.values() if s.get("status") == "healthy")
        return {
            "total": len(services),
            "healthy": healthy,
            "unhealthy": len(services) - healthy,
        }
    except Exception:
        return {"total": 0, "healthy": 0, "unhealthy": 0}


def _collect_job_metrics() -> dict[str, Any]:
    try:
        from media_stack.cli.commands.job_framework import get_job_history
        history = get_job_history()
        if not history:
            return {"runs_24h": 0, "ok": 0, "errors": 0, "avg_duration_s": 0}
        cutoff = time.time() - 86400
        recent = [h for h in history if h.get("ts", 0) > cutoff]
        ok = sum(h.get("ok", 0) for h in recent)
        errors = sum(h.get("errors", 0) for h in recent)
        durations = [h.get("elapsed", 0) for h in recent if h.get("elapsed")]
        return {
            "runs_24h": len(recent),
            "ok": ok,
            "errors": errors,
            "avg_duration_s": round(sum(durations) / max(len(durations), 1), 1),
        }
    except Exception:
        return {"runs_24h": 0, "ok": 0, "errors": 0, "avg_duration_s": 0}


def _collect_media_metrics() -> dict[str, Any]:
    media: dict[str, Any] = {}
    # Libraries
    try:
        from media_stack.api.services.config import get_libraries
        libs = get_libraries()
        media["libraries"] = len(libs.get("libraries", []))
    except Exception:
        media["libraries"] = 0
    # Live TV
    try:
        from media_stack.api.services.config import get_livetv_sources
        ltv = get_livetv_sources()
        media["livetv_tuners"] = len(ltv.get("tuners", []))
    except Exception:
        media["livetv_tuners"] = 0
    # Indexers
    try:
        from media_stack.api.services.content import get_indexers
        idx = get_indexers()
        media["indexers"] = idx.get("total", 0)
    except Exception:
        media["indexers"] = 0
    # Storage
    try:
        from media_stack.api.services.disk import get_disk_usage
        disk = get_disk_usage()
        media["storage_gb"] = round(disk.get("used_bytes", 0) / (1024**3), 1)
    except Exception:
        media["storage_gb"] = 0
    # Download stats (from qBittorrent)
    try:
        from media_stack.api.services.content import get_downloads
        dl = get_downloads()
        active = dl.get("downloads", [])
        media["active_downloads"] = len(active)
        media["download_speed_mbps"] = round(
            sum(d.get("dlspeed", 0) for d in active) / (1024 * 1024), 1
        )
        media["upload_speed_mbps"] = round(
            sum(d.get("upspeed", 0) for d in active) / (1024 * 1024), 1
        )
    except Exception:
        media["active_downloads"] = 0
    return media


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------

def _buffer_path() -> Path:
    return Path(_config_root()) / ".controller" / "telemetry-buffer.json"


def _buffer_payload(payload: dict[str, Any]) -> None:
    """Buffer a failed payload to disk for retry."""
    path = _buffer_path()
    try:
        existing = json.loads(path.read_text()) if path.is_file() else []
        if not isinstance(existing, list):
            existing = []
        existing.append(payload)
        # Keep max 48 buffered (2 days at hourly)
        if len(existing) > 48:
            existing = existing[-48:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing))
    except Exception:
        pass


def _drain_buffer(endpoint: str, api_key: str) -> int:
    """Push buffered payloads. Returns count sent."""
    path = _buffer_path()
    if not path.is_file():
        return 0
    try:
        entries = json.loads(path.read_text())
        if not isinstance(entries, list) or not entries:
            return 0
    except Exception:
        return 0
    sent = 0
    remaining = []
    for entry in entries:
        if _push_one(endpoint, api_key, entry):
            sent += 1
        else:
            remaining.append(entry)
    try:
        if remaining:
            path.write_text(json.dumps(remaining))
        else:
            path.unlink(missing_ok=True)
    except Exception:
        pass
    return sent


# Schema v1: positional array format — no keys transmitted.
# Both client and server must agree on field order.
_SCHEMA_VERSION = 1
_SCHEMA_FIELDS = [
    "cluster_id", "cluster_name", "ts",
    "controller.version", "controller.platform", "controller.uptime_hours",
    "services.total", "services.healthy",
    "jobs.runs_24h", "jobs.ok", "jobs.errors", "jobs.avg_duration_s",
    "media.libraries", "media.livetv_tuners", "media.indexers",
    "media.storage_gb", "media.active_downloads",
    "media.download_speed_mbps", "media.upload_speed_mbps",
]


def _to_compact(payload: dict[str, Any]) -> list[Any]:
    """Convert full payload to positional array (4x smaller)."""
    def _get(d: dict, dotted: str) -> Any:
        for k in dotted.split("."):
            if isinstance(d, dict):
                d = d.get(k, 0)
            else:
                return 0
        return d
    return [_get(payload, f) for f in _SCHEMA_FIELDS]


def _from_compact(arr: list[Any]) -> dict[str, Any]:
    """Reconstruct full payload from positional array."""
    result: dict[str, Any] = {}
    for i, field in enumerate(_SCHEMA_FIELDS):
        val = arr[i] if i < len(arr) else 0
        parts = field.split(".")
        if len(parts) == 1:
            result[parts[0]] = val
        else:
            result.setdefault(parts[0], {})[parts[1]] = val
    return result


def _push_one(endpoint: str, api_key: str, payload: dict[str, Any]) -> bool:
    """Push a single payload. Uses compact array + gzip for efficiency."""
    import gzip
    try:
        # Send compact array with schema version header
        compact = [_SCHEMA_VERSION] + _to_compact(payload)
        raw = json.dumps(compact, separators=(",", ":")).encode("utf-8")
        compressed = gzip.compress(raw)
        req = urllib.request.Request(
            endpoint,
            data=compressed,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "X-Schema-Version": str(_SCHEMA_VERSION),
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "media-stack-telemetry/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201, 202, 204)
    except Exception:
        # Fallback to full JSON if compact fails
        try:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            req = urllib.request.Request(
                endpoint, data=data, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": "media-stack-telemetry/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201, 202, 204)
        except Exception:
            return False


def push_telemetry(log: Any = None) -> dict[str, Any]:
    """Collect metrics and push to central server. Buffers on failure."""
    endpoint = os.environ.get("TELEMETRY_ENDPOINT", "").strip()
    api_key = os.environ.get("TELEMETRY_API_KEY", "").strip()
    if not endpoint:
        return {"status": "disabled", "reason": "TELEMETRY_ENDPOINT not set"}

    metrics = collect_metrics()

    # Try to drain buffer first
    drained = _drain_buffer(endpoint, api_key)
    if drained and log:
        log(f"[INFO] Telemetry: pushed {drained} buffered payloads")

    # Push current
    if _push_one(endpoint, api_key, metrics):
        if log:
            log("[OK] Telemetry: metrics pushed")
        return {"status": "ok", "cluster_id": metrics["cluster_id"]}
    else:
        _buffer_payload(metrics)
        if log:
            log("[WARN] Telemetry: push failed, buffered locally")
        return {"status": "buffered", "cluster_id": metrics["cluster_id"]}


# ---------------------------------------------------------------------------
# Scheduler — call from controller_serve.py
# ---------------------------------------------------------------------------

def start_telemetry_timer(log: Any = None) -> None:
    """Start background telemetry push on a schedule."""
    enabled = os.environ.get("TELEMETRY_ENABLED", "0") == "1"
    if not enabled:
        return
    interval = int(os.environ.get("TELEMETRY_INTERVAL_SECONDS", "3600"))
    import threading

    def _loop():
        import time as _t
        _t.sleep(60)  # Wait for services to start
        while True:
            try:
                push_telemetry(log=log)
            except Exception:
                pass
            _t.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="telemetry")
    t.start()
    if log:
        log(f"[INFO] Telemetry: enabled (interval={interval}s, endpoint={os.environ.get('TELEMETRY_ENDPOINT', 'not set')})")
