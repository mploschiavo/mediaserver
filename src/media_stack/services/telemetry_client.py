"""Telemetry client -- collects cluster metrics and pushes to central server.

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
import logging


# Schema v1: positional array format -- no keys transmitted.
# Both client and server must agree on field order.
_SCHEMA_VERSION = 1
_SCHEMA_FIELDS = [
    "cluster_id", "cluster_name", "ts",
    "controller.version", "controller.platform", "controller.uptime_hours",
    "services.total", "services.healthy",
    "jobs.runs_24h", "jobs.ok", "jobs.errors", "jobs.avg_duration_s",
    "media.libraries", "media.livetv_tuners", "media.indexers",
    "media.storage_gb", "media.active_downloads",
    "media.torrent_rx_gb", "media.torrent_tx_gb",
    "network.rx_gb", "network.tx_gb", "network.containers",
]


class TelemetryClient:
    """Collects cluster metrics and pushes to a central telemetry server."""

    # Transport state
    _udp_ok: bool | None = None
    _udp_last_probe: float = 0
    _UDP_PROBE_INTERVAL = 3600

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _config_root() -> str:
        return os.environ.get("CONFIG_ROOT", "/srv-config")

    def _cluster_id(self) -> str:
        """Persistent cluster ID -- generated once, stored to disk."""
        explicit = os.environ.get("TELEMETRY_CLUSTER_ID", "").strip()
        if explicit:
            return explicit
        id_file = Path(self._config_root()) / ".controller" / "cluster-id"
        if id_file.is_file():
            return id_file.read_text().strip()
        cid = str(uuid.uuid4())
        try:
            id_file.parent.mkdir(parents=True, exist_ok=True)
            id_file.write_text(cid)
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        return cid

    @staticmethod
    def _cluster_name() -> str:
        return os.environ.get("TELEMETRY_CLUSTER_NAME",
                              os.environ.get("COMPOSE_PROJECT_NAME",
                                             socket.gethostname()))

    @staticmethod
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
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        try:
            with open("/proc/uptime") as f:
                info["uptime_hours"] = round(float(f.read().split()[0]) / 3600, 1)
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        return info

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def _collect_network_io() -> dict[str, Any]:
        """Collect total RX/TX bytes across all stack containers."""
        io: dict[str, Any] = {"rx_gb": 0, "tx_gb": 0, "containers": 0, "per_container": {}}
        try:
            import docker
            client = docker.from_env()
            for c in client.containers.list():
                try:
                    stats = c.stats(stream=False)
                    networks = stats.get("networks", {})
                    rx = sum(n.get("rx_bytes", 0) for n in networks.values())
                    tx = sum(n.get("tx_bytes", 0) for n in networks.values())
                    io["per_container"][c.name] = {
                        "rx_mb": round(rx / (1024 * 1024), 1),
                        "tx_mb": round(tx / (1024 * 1024), 1),
                    }
                    io["rx_gb"] += rx
                    io["tx_gb"] += tx
                    io["containers"] += 1
                except Exception as exc:
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                    continue
            io["rx_gb"] = round(io["rx_gb"] / (1024**3), 2)
            io["tx_gb"] = round(io["tx_gb"] / (1024**3), 2)
            return io
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        try:
            with open("/proc/net/dev") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 10 or ":" not in parts[0]:
                        continue
                    iface = parts[0].rstrip(":")
                    if iface in ("lo",):
                        continue
                    io["rx_gb"] += int(parts[1])
                    io["tx_gb"] += int(parts[9])
            io["rx_gb"] = round(io["rx_gb"] / (1024**3), 2)
            io["tx_gb"] = round(io["tx_gb"] / (1024**3), 2)
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        return io

    @staticmethod
    def _collect_media_metrics() -> dict[str, Any]:
        media: dict[str, Any] = {}
        try:
            from media_stack.api.services.config import get_libraries
            libs = get_libraries()
            media["libraries"] = len(libs.get("libraries", []))
        except Exception:
            media["libraries"] = 0
        try:
            from media_stack.api.services.config import get_livetv_sources
            ltv = get_livetv_sources()
            media["livetv_tuners"] = len(ltv.get("tuners", []))
        except Exception:
            media["livetv_tuners"] = 0
        try:
            from media_stack.api.services.content import get_indexers
            idx = get_indexers()
            media["indexers"] = idx.get("total", 0)
        except Exception:
            media["indexers"] = 0
        try:
            from media_stack.api.services.disk import get_disk_usage
            disk = get_disk_usage()
            media["storage_gb"] = round(disk.get("used_bytes", 0) / (1024**3), 1)
        except Exception:
            media["storage_gb"] = 0
        try:
            import docker
            from media_stack.api.services.registry import SERVICE_MAP
            tc_id = ""
            try:
                from media_stack.api.services.config import _load_profile_yaml
                data, _ = _load_profile_yaml()
                tc_id = data.get("technology_bindings", {}).get("torrent_client", "")
            except Exception as exc:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                pass
            if tc_id:
                client = docker.from_env()
                for c in client.containers.list():
                    if tc_id in c.name.lower():
                        stats = c.stats(stream=False)
                        nets = stats.get("networks", {})
                        media["torrent_rx_gb"] = round(sum(n.get("rx_bytes", 0) for n in nets.values()) / (1024**3), 2)
                        media["torrent_tx_gb"] = round(sum(n.get("tx_bytes", 0) for n in nets.values()) / (1024**3), 2)
                        break
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        try:
            from media_stack.api.services.content import get_downloads
            dl = get_downloads()
            media["active_downloads"] = len(dl.get("downloads", []))
        except Exception:
            media["active_downloads"] = 0
        return media

    def _buffer_path(self) -> Path:
        return Path(self._config_root()) / ".controller" / "telemetry-buffer.json"

    def _buffer_payload(self, payload: dict[str, Any]) -> None:
        """Buffer a failed payload to disk for retry."""
        path = self._buffer_path()
        try:
            existing = json.loads(path.read_text()) if path.is_file() else []
            if not isinstance(existing, list):
                existing = []
            existing.append(payload)
            if len(existing) > 48:
                existing = existing[-48:]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(existing))
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass

    def _drain_buffer(self, endpoint: str, api_key: str) -> int:
        """Push buffered payloads. Returns count sent."""
        path = self._buffer_path()
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
            if self._push_one(endpoint, api_key, entry):
                sent += 1
            else:
                remaining.append(entry)
        try:
            if remaining:
                path.write_text(json.dumps(remaining))
            else:
                path.unlink(missing_ok=True)
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        return sent

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def _parse_host_port(endpoint: str) -> tuple[str, int]:
        """Extract host and port from endpoint URL."""
        from urllib.parse import urlparse
        parsed = urlparse(endpoint)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8200
        return host, port

    def _probe_udp(self, endpoint: str, api_key: str) -> bool:
        """Test if UDP works to the server."""
        import hashlib
        import socket as _socket
        host, port = self._parse_host_port(endpoint)
        udp_port = port + 1
        cid = self._cluster_id()[:8]
        key_hash = hashlib.md5((api_key or "").encode()).hexdigest()[:8]
        ping = f"PING:{key_hash}:{cid}".encode()
        try:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            sock.settimeout(2.0)
            sock.sendto(ping, (host, udp_port))
            data, _ = sock.recvfrom(64)
            sock.close()
            return data.strip().startswith(b"PONG")
        except Exception:
            return False

    def _send_udp(self, endpoint: str, api_key: str, payload: dict[str, Any]) -> bool:
        """Send payload via UDP."""
        import gzip
        import hashlib
        import socket as _socket
        host, port = self._parse_host_port(endpoint)
        udp_port = port + 1
        try:
            compact = [_SCHEMA_VERSION] + self._to_compact(payload)
            raw = json.dumps(compact, separators=(",", ":")).encode()
            compressed = gzip.compress(raw)
            key_hash = hashlib.md5((api_key or "").encode()).hexdigest()[:8].encode()
            datagram = key_hash + b":" + compressed
            if len(datagram) > 1400:
                return False
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            sock.sendto(datagram, (host, udp_port))
            sock.close()
            return True
        except Exception:
            return False

    @staticmethod
    def _send_tcp(endpoint: str, api_key: str, payload: dict[str, Any]) -> bool:
        """Send payload via TCP/HTTP POST with compact gzip."""
        import gzip
        try:
            compact = [_SCHEMA_VERSION] + TelemetryClient._to_compact(payload)
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
            return False

    def _push_one(self, endpoint: str, api_key: str, payload: dict[str, Any]) -> bool:
        """Push a single payload. Tries UDP first, falls back to TCP."""
        now = time.time()
        if self._udp_ok is None or (now - self._udp_last_probe > self._UDP_PROBE_INTERVAL):
            TelemetryClient._udp_ok = self._probe_udp(endpoint, api_key)
            TelemetryClient._udp_last_probe = now

        if self._udp_ok:
            if self._send_udp(endpoint, api_key, payload):
                return True
            TelemetryClient._udp_ok = False

        return self._send_tcp(endpoint, api_key, payload)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def collect_metrics(self) -> dict[str, Any]:
        """Collect all cluster metrics. Safe -- never raises."""
        metrics: dict[str, Any] = {
            "cluster_id": self._cluster_id(),
            "cluster_name": self._cluster_name(),
            "ts": time.time(),
            "controller": self._collect_controller_info(),
            "services": self._collect_service_health(),
            "jobs": self._collect_job_metrics(),
            "media": self._collect_media_metrics(),
            "network": self._collect_network_io(),
        }
        return metrics

    def push_telemetry(self, log: Any = None) -> dict[str, Any]:
        """Collect metrics and push to central server. Buffers on failure."""
        endpoint = os.environ.get("TELEMETRY_ENDPOINT", "").strip()
        api_key = os.environ.get("TELEMETRY_API_KEY", "").strip()
        if not endpoint:
            return {"status": "disabled", "reason": "TELEMETRY_ENDPOINT not set"}

        metrics = self.collect_metrics()

        drained = self._drain_buffer(endpoint, api_key)
        if drained and log:
            log(f"[INFO] Telemetry: pushed {drained} buffered payloads")

        if self._push_one(endpoint, api_key, metrics):
            if log:
                log("[OK] Telemetry: metrics pushed")
            return {"status": "ok", "cluster_id": metrics["cluster_id"]}
        else:
            self._buffer_payload(metrics)
            if log:
                log("[WARN] Telemetry: push failed, buffered locally")
            return {"status": "buffered", "cluster_id": metrics["cluster_id"]}

    def start_telemetry_timer(self, log: Any = None) -> None:
        """Start background telemetry push on a schedule."""
        enabled = os.environ.get("TELEMETRY_ENABLED", "0") == "1"
        if not enabled:
            return
        interval = int(os.environ.get("TELEMETRY_INTERVAL_SECONDS", "3600"))
        import threading

        def _loop():
            import time as _t
            _t.sleep(60)
            while True:
                try:
                    self.push_telemetry(log=log)
                except Exception as exc:
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                    pass
                _t.sleep(interval)

        t = threading.Thread(target=_loop, daemon=True, name="telemetry")
        t.start()
        if log:
            log(f"[INFO] Telemetry: enabled (interval={interval}s, endpoint={os.environ.get('TELEMETRY_ENDPOINT', 'not set')})")


# ---------------------------------------------------------------------------
# Singleton + backward-compat module-level references
# ---------------------------------------------------------------------------

_instance = TelemetryClient()
collect_metrics = _instance.collect_metrics
push_telemetry = _instance.push_telemetry
start_telemetry_timer = _instance.start_telemetry_timer
_to_compact = _instance._to_compact
_from_compact = _instance._from_compact
_cluster_id = _instance._cluster_id
_cluster_name = _instance._cluster_name
_buffer_payload = _instance._buffer_payload
_buffer_path = _instance._buffer_path
_drain_buffer = _instance._drain_buffer
_push_one = _instance._push_one
_probe_udp = _instance._probe_udp
_send_udp = _instance._send_udp
_config_root = _instance._config_root
_collect_controller_info = _instance._collect_controller_info
_collect_service_health = _instance._collect_service_health
_collect_job_metrics = _instance._collect_job_metrics
_collect_network_io = _instance._collect_network_io
_collect_media_metrics = _instance._collect_media_metrics
_parse_host_port = _instance._parse_host_port
_send_tcp = _instance._send_tcp
_udp_ok = _instance._udp_ok
_udp_last_probe = _instance._udp_last_probe
_UDP_PROBE_INTERVAL = _instance._UDP_PROBE_INTERVAL
_parse_host_port = _instance._parse_host_port
_send_tcp = _instance._send_tcp
_UDP_PROBE_INTERVAL = _instance._UDP_PROBE_INTERVAL
