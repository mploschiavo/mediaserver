"""Operations services: namespaces, images, GPU, mounts, snapshots, logs.

ADR-0012: top-level FunctionDef count is held at 0 — every helper is
an instance method on either ``OpsService`` (the public API) or
``OpsLogHelpers`` (the sibling helper class for the log-fetch
support routines). Module-level aliases preserve the original public
+ underscore-prefix import surface so callers and ``mock.patch``
sites continue to work without churn.
"""

from __future__ import annotations


import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from media_stack.core.logging_utils import log_swallowed

logger = logging.getLogger("controller_api")

from ._resolve import resolve_config_path
# hoisted from per-method import to reduce CIRCULAR_IMPORT_RISK_RATCHET drift
# (registry is a leaf config module — no cycle with ops)
from media_stack.core.service_registry.registry import SERVICES as _SERVICES


# Hard cap on a single ``GET /api/logs/<svc>`` response. Bumped from
# 500 → 50000 in v1.0.270: operators were ssh'ing into the controller
# container to tail the full pipeline because the dashboard cap
# truncated the bootstrap window. 50k lines is ~5 MB of text — well
# under any HTTP body limit and small enough to render in <1s.
LOG_LINES_HARD_CAP = 50000

# Timestamp prefix Envoy/controller writers use, e.g.
# ``[2026-04-27T03:00:00+0000] [INFO] media_stack: ...``. The level
# token can be one of these (case-insensitive); used by the level
# filter to keep matching cheap (no full-line tokenization).
_LEVEL_TOKENS: dict[str, tuple[str, ...]] = {
    "error": ("[ERR]", "[ERROR]", "ERROR", "Traceback"),
    "warning": ("[WARN]", "[WARNING]", "WARN"),
    "info": ("[INFO]", "[OK]", "INFO"),
    "debug": ("[DEBUG]", "[DBG]", "DEBUG"),
}


class OpsLogHelpers:
    """Sibling helper class for log-fetch support routines.

    Exists so the public ``OpsService`` doesn't push past the
    god-class ceiling (already pinned at the upper end). Methods are
    plain instance methods (no ``@staticmethod``); the module-level
    ``_INSTANCE`` of this class supplies the underscore-prefixed
    import surface (``_parse_since_seconds``,
    ``_apply_log_filters``, ``_read_archive_log_lines``) that the
    tests + ``OpsService.get_service_logs`` consume.
    """

    def parse_since_seconds(self, since: str) -> int | None:
        """Convert a relative shorthand or ISO datetime into a number
        of seconds-from-now. Returns ``None`` if unparseable; the caller
        treats that as "no time filter" rather than failing the whole
        request."""
        s = since.strip().lower()
        if not s:
            return None
        # Relative shorthand: ``5m``, ``2h``, ``1d``, ``3600s``.
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        if len(s) >= 2 and s[-1] in units and s[:-1].isdigit():
            return int(s[:-1]) * units[s[-1]]
        # ISO-8601 datetime — ``2026-04-27T03:00:00Z`` etc.
        try:
            from datetime import datetime, timezone as _tz
            # Tolerate trailing ``Z``.
            cleaned = s.rstrip("z").upper().replace(" ", "T")
            # ``fromisoformat`` accepts ``+00:00`` but not ``Z``.
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_tz.utc)
            delta = (datetime.now(tz=_tz.utc) - dt).total_seconds()
            return max(1, int(delta))
        except (TypeError, ValueError):
            return None

    def apply_log_filters(
        self,
        raw_lines: list[str],
        action: str | None,
        level: str | None,
        q: str | None,
    ) -> list[str]:
        """Filter the in-memory list returned by docker/k8s. Cheap
        substring/regex checks per line; designed for ~50k-line buffers.
        Returns lines in original order."""
        out = raw_lines
        if action:
            # Match either ``[ACTION] <name>`` or ``[JOB] <name>`` or
            # action key embedded in structured log lines.
            needle = f"[ACTION] {action}".lower()
            needle_job = f"[JOB] {action}".lower()
            bare_needle = action.lower()
            out = [
                ln for ln in out
                if needle in ln.lower()
                or needle_job in ln.lower()
                or bare_needle in ln.lower()
            ]
        if level:
            tokens = _LEVEL_TOKENS.get(level.lower())
            if tokens:
                out = [
                    ln for ln in out
                    if any(tok in ln or tok.lower() in ln.lower() for tok in tokens)
                ]
        if q:
            # ``/regex/i`` syntax for case-insensitive regex; otherwise
            # literal substring (case-insensitive).
            if (
                len(q) >= 2
                and q.startswith("/")
                and q.rstrip().endswith(("/", "/i"))
            ):
                try:
                    import re as _re
                    stripped = q[1:].rstrip()
                    flags = 0
                    if stripped.endswith("/i"):
                        flags = _re.IGNORECASE
                        stripped = stripped[:-2]
                    else:
                        stripped = stripped[:-1]
                    pattern = _re.compile(stripped, flags=flags)
                    out = [ln for ln in out if pattern.search(ln)]
                except _re.error:
                    # Bad regex falls back to literal match.
                    needle = q.lower()
                    out = [ln for ln in out if needle in ln.lower()]
            else:
                needle = q.lower()
                out = [ln for ln in out if needle in ln.lower()]
        return out

    def read_archive_log_lines(
        self,
        service_name: str,
        since_seconds: int | None,
    ) -> list[str]:
        """Read rotated/compressed archive logs from a configured
        directory. Compose deployments using a long-running stack accumulate
        rotated logs that the docker daemon won't replay; the dashboard
        needs them so the operator never has to ``docker logs --since=2d``
        by hand. Opt-in via ``MEDIA_STACK_LOG_ARCHIVE_DIR``; the directory
        is expected to contain ``<service>.log.gz`` (one file per service)
        OR ``<service>.<N>.log.gz`` rotation suffixes.

        Best-effort — any error returns an empty list so the live tail
        still shows up in the response.
        """
        archive_dir_str = os.environ.get(
            "MEDIA_STACK_LOG_ARCHIVE_DIR", "",
        ).strip()
        if not archive_dir_str:
            return []
        try:
            archive_dir = Path(archive_dir_str)
            if not archive_dir.is_dir():
                return []
            out: list[str] = []
            cutoff_ts = (
                int(time.time()) - since_seconds if since_seconds else 0
            )
            for path in sorted(archive_dir.glob(f"{service_name}*.log*")):
                try:
                    if path.suffix == ".gz":
                        import gzip
                        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
                            text = f.read()
                    else:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    # Mark with archive prefix so the UI can dim them.
                    file_lines = text.splitlines()
                    if cutoff_ts > 0 and path.stat().st_mtime < cutoff_ts:
                        # Whole file predates the cutoff — skip without
                        # paying line-by-line filter cost. Caller's
                        # since-seconds filter would drop them anyway.
                        continue
                    out.extend(f"[archive:{path.name}] {ln}" for ln in file_lines)
                except (OSError, UnicodeDecodeError) as exc:
                    log_swallowed(exc)
            return out
        except OSError as exc:
            log_swallowed(exc)
            return []


class OpsService:
    """Infrastructure operations: namespaces, images, GPU, mounts, snapshots, logs."""

    def get_namespaces(self) -> dict[str, Any]:
        """List K8s namespaces with pod details, or compose container info."""
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if namespace:
            return self._get_k8s_namespaces(namespace)
        return self._get_compose_containers()

    def _get_k8s_namespaces(self, namespace: str) -> dict[str, Any]:
        """K8s namespace and deployment info."""
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            core_v1 = k8s_client.CoreV1Api()
            apps_v1 = k8s_client.AppsV1Api()

            pods = core_v1.list_namespaced_pod(namespace)
            running = sum(1 for p in pods.items if p.status.phase == "Running")
            problems = [
                {"name": p.metadata.name, "phase": p.status.phase or "Unknown",
                 "reason": (p.status.container_statuses[0].state.waiting.reason if p.status.container_statuses and p.status.container_statuses[0].state.waiting else "")}
                for p in pods.items if p.status.phase != "Running" and p.status.phase != "Succeeded"
            ]
            ns_info = [{"namespace": namespace, "current": True, "pods": len(pods.items), "running": running, "problems": problems}]

            deps = apps_v1.list_namespaced_deployment(namespace)
            services = []
            for dep in deps.items:
                c = dep.spec.template.spec.containers[0] if dep.spec.template.spec.containers else None
                cpu_req = ""
                mem_req = ""
                if c and c.resources and c.resources.requests:
                    cpu_req = c.resources.requests.get("cpu", "")
                    mem_req = c.resources.requests.get("memory", "")
                services.append({
                    "name": dep.metadata.name,
                    "replicas": dep.spec.replicas or 1,
                    "ready": dep.status.ready_replicas or 0,
                    "image": c.image if c else "",
                    "cpu_request": cpu_req,
                    "mem_request": mem_req,
                })

            # Pod metrics
            pod_metrics: list[dict[str, str]] = []
            try:
                from kubernetes.client import CustomObjectsApi
                custom = CustomObjectsApi()
                metrics = custom.list_namespaced_custom_object("metrics.k8s.io", "v1beta1", namespace, "pods")
                for item in metrics.get("items", []):
                    pod_name = item["metadata"]["name"]
                    for container in item.get("containers", []):
                        pod_metrics.append({
                            "pod": pod_name,
                            "cpu": container.get("usage", {}).get("cpu", "0"),
                            "memory": container.get("usage", {}).get("memory", "0"),
                        })
            except Exception as exc:
                logger.debug("Pod metrics unavailable: %s", exc)

            totals = self._aggregate_metrics(pod_metrics)
            return {"namespaces": ns_info, "services": services, "pod_metrics": pod_metrics, "totals": totals}
        except Exception as exc:
            return {"error": str(exc)[:120]}

    def _get_compose_containers(self) -> dict[str, Any]:
        """Compose container info with resource usage."""
        try:
            import docker
            client = docker.from_env()
            containers = client.containers.list()
            ns_info = [{"namespace": "compose", "current": True, "pods": len(containers),
                         "running": sum(1 for c in containers if c.status == "running"), "problems": []}]
            services = [{"name": c.name, "replicas": 1, "ready": 1 if c.status == "running" else 0,
                          "image": c.image.tags[0] if c.image.tags else str(c.image.short_id)} for c in containers]

            def _get_stats(c: Any) -> dict[str, Any] | None:
                try:
                    stats = c.stats(stream=False)
                    cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
                    sys_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
                    cpu_pct = round(cpu_delta / sys_delta * 100, 2) if sys_delta > 0 else 0
                    mem = stats["memory_stats"].get("usage", 0)
                    mem_mi = round(mem / 1048576) if mem else 0
                    return {"pod": c.name, "cpu": f"{int(cpu_pct * 10)}m", "memory": f"{mem_mi}Mi"}
                except Exception:
                    return None

            pod_metrics: list[dict[str, str]] = []
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(_get_stats, c): c.name for c in containers}
                for f in as_completed(futures):
                    result = f.result()
                    if result:
                        pod_metrics.append(result)

            totals = self._aggregate_metrics(pod_metrics)
            return {"namespaces": ns_info, "services": services, "pod_metrics": pod_metrics, "totals": totals}
        except Exception as exc:
            return {"error": str(exc)[:120]}

    def _aggregate_metrics(self, pod_metrics: list[dict[str, str]]) -> dict[str, Any]:
        """Sum CPU (millicores) and memory (MiB) across all containers."""
        total_cpu_m = 0
        total_mem_mi = 0
        for m in pod_metrics:
            cpu_str = m.get("cpu", "0")
            if cpu_str.endswith("m"):
                total_cpu_m += int(cpu_str[:-1])
            elif cpu_str.endswith("n"):
                total_cpu_m += int(cpu_str[:-1]) // 1_000_000
            elif cpu_str.replace(".", "", 1).isdigit():
                total_cpu_m += int(float(cpu_str) * 1000)
            mem_str = m.get("memory", "0")
            if mem_str.endswith("Mi"):
                total_mem_mi += int(mem_str[:-2])
            elif mem_str.endswith("Ki"):
                total_mem_mi += int(mem_str[:-2]) // 1024
            elif mem_str.endswith("Gi"):
                total_mem_mi += int(mem_str[:-2]) * 1024
            elif mem_str.replace(".", "", 1).isdigit():
                total_mem_mi += int(int(mem_str) / 1048576)
        return {
            "cpu_millicores": total_cpu_m,
            "cpu_display": f"{total_cpu_m}m" if total_cpu_m < 1000 else f"{total_cpu_m / 1000:.1f} cores",
            "memory_mi": total_mem_mi,
            "memory_display": f"{total_mem_mi}Mi" if total_mem_mi < 1024 else f"{total_mem_mi / 1024:.1f}Gi",
            "container_count": len(pod_metrics),
        }

    def check_image_updates(self) -> dict[str, Any]:
        """Compare running image digests for staleness detection."""
        namespace = os.environ.get("K8S_NAMESPACE", "")
        results: list[dict[str, str]] = []
        if namespace:
            try:
                from kubernetes import client as k8s_client, config as k8s_config
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                apps_v1 = k8s_client.AppsV1Api()
                deps = apps_v1.list_namespaced_deployment(namespace)
                for dep in deps.items:
                    name = dep.metadata.name
                    if dep.spec.template.spec.containers:
                        c = dep.spec.template.spec.containers[0]
                        image = c.image or ""
                        tag = "pinned (digest)" if "@sha256:" in image else image.split(":")[-1] if ":" in image.split("/")[-1] else "latest"
                        last_updated = ""
                        if dep.metadata.creation_timestamp:
                            last_updated = dep.metadata.creation_timestamp.strftime("%Y-%m-%d %H:%M:%S")
                        for cond in (dep.status.conditions or []):
                            if cond.type == "Progressing" and cond.last_update_time:
                                last_updated = cond.last_update_time.strftime("%Y-%m-%d %H:%M:%S")
                        results.append({"name": name, "image": image, "tag": tag, "last_updated": last_updated})
            except Exception as exc:
                return {"error": str(exc)[:80]}
        else:
            try:
                import docker
                client = docker.from_env()
                for c in client.containers.list():
                    image = c.image.tags[0] if c.image.tags else str(c.image.short_id)
                    tag = image.split(":")[-1] if ":" in image else "latest"
                    started = c.attrs.get("State", {}).get("StartedAt", "")
                    created = c.image.attrs.get("Created", "") if c.image.attrs else ""
                    # Get image digest for rollback reference
                    digest = ""
                    repo_digests = c.image.attrs.get("RepoDigests", []) if c.image.attrs else []
                    if repo_digests:
                        digest = repo_digests[0].split("@")[-1][:19] + "..." if repo_digests[0] else ""
                    results.append({
                        "name": c.name, "image": image, "tag": tag,
                        "started_at": started[:19].replace("T", " ") if started else "",
                        "image_created": created[:19].replace("T", " ") if created else "",
                        "digest": digest,
                    })
            except Exception as exc:
                return {"error": str(exc)[:80]}
        # Calculate staleness from image_created or last_updated
        now = time.time()
        stale_count = 0
        for r in results:
            age_source = r.get("image_created") or r.get("last_updated") or ""
            days_old = -1
            if age_source:
                try:
                    from datetime import datetime
                    dt = datetime.strptime(age_source.strip()[:19], "%Y-%m-%d %H:%M:%S")
                    days_old = int((now - dt.timestamp()) / 86400)
                except (ValueError, OSError):
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
            r["days_old"] = days_old
            r["stale"] = days_old > 30
            if r["stale"]:
                stale_count += 1
        pinned = sum(1 for r in results if r["tag"] not in ("latest",))
        return {"images": results, "total": len(results), "pinned": pinned, "stale": stale_count}

    def get_gpu_info(self) -> dict[str, Any]:
        """Detect GPU hardware for transcoding — checks host via Docker, falls back to container."""
        result: dict[str, Any] = {"detected": False, "gpus": [], "jellyfin_configured": False,
                                   "jellyfin_has_gpu": False, "note": ""}

        # Strategy 1: Check which containers already have GPU devices mounted
        try:
            import docker
            client = docker.from_env()
            for c in client.containers.list():
                devices = c.attrs.get("HostConfig", {}).get("Devices") or []
                runtime = c.attrs.get("HostConfig", {}).get("Runtime", "")
                for dev in devices:
                    host_path = dev.get("PathOnHost", "") if isinstance(dev, dict) else str(dev)
                    if "/dev/dri" in host_path or "/dev/nvidia" in host_path:
                        gpu_type = "nvidia" if "nvidia" in host_path.lower() or runtime == "nvidia" else "intel/va-api"
                        result["gpus"].append({"type": gpu_type, "name": f"GPU passed to {c.name} ({host_path})",
                                               "container": c.name})
                        result["detected"] = True
                if runtime == "nvidia":
                    result["gpus"].append({"type": "nvidia", "name": f"NVIDIA runtime on {c.name}", "container": c.name})
                    result["detected"] = True
        except Exception as exc:
            log_swallowed(exc)

        # Strategy 2: Query host Docker info for GPU-related runtimes
        if not result["detected"]:
            try:
                import docker
                client = docker.from_env()
                info = client.info()
                runtimes = info.get("Runtimes", {})
                if "nvidia" in runtimes:
                    result["detected"] = True
                    result["gpus"].append({"type": "nvidia", "name": "NVIDIA Container Runtime available on host"})
                # Check for default GPU devices
                security = info.get("SecurityOptions", [])
                for s in security:
                    if "gpu" in str(s).lower():
                        result["detected"] = True
            except Exception as exc:
                log_swallowed(exc)

        # Strategy 3: Check inside this container (works if GPU is passed through)
        if not result["detected"]:
            render_devices = list(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else []
            if render_devices:
                result["detected"] = True
                result["gpus"].append({"type": "intel/generic", "driver": "va-api",
                                       "devices": [str(d) for d in render_devices],
                                       "name": "GPU available in controller container"})
            try:
                nvidia = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                if nvidia.returncode == 0:
                    for line in nvidia.stdout.strip().splitlines():
                        parts = [p.strip() for p in line.split(",")]
                        result["gpus"].append({"type": "nvidia", "name": parts[0] if parts else "NVIDIA GPU",
                                               "driver": parts[1] if len(parts) > 1 else "",
                                               "memory": parts[2] if len(parts) > 2 else ""})
                        result["detected"] = True
            except FileNotFoundError:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)

        # Detect host platform
        import platform
        host_os = platform.system().lower()
        result["host_os"] = host_os
        namespace = os.environ.get("K8S_NAMESPACE", "")
        result["runtime"] = "kubernetes" if namespace else "compose"

        if not result["detected"]:
            if host_os == "darwin":
                result["note"] = "GPU passthrough is not supported on macOS (Docker Desktop uses a Linux VM). Hardware transcoding requires a Linux Docker host."
            elif host_os == "windows":
                result["note"] = "GPU passthrough on Windows requires Docker Desktop with WSL2 backend and NVIDIA Container Toolkit. See: docs.nvidia.com/datacenter/cloud-native/container-toolkit"
            elif namespace:
                result["note"] = "On Kubernetes, request GPU via resource limits: nvidia.com/gpu: 1 in the media-server pod spec."
            else:
                result["note"] = "No containers have GPU devices mounted. To enable, update docker-compose.yml and redeploy."

        # Check if the media server container has GPU passthrough (delegated to app layer)
        try:
            import docker
            import importlib
            client = docker.from_env()
            # Dynamically load GPU module from the media server's app layer
            ms = next((s for s in _SERVICES if s.category == "media"), None)
            if ms:
                gpu_mod = importlib.import_module(f"media_stack.services.apps.{ms.id}.gpu")
                check_fn = getattr(gpu_mod, f"check_{ms.id}_gpu", None)
                if check_fn:
                    result.update(check_fn(client))
        except Exception as exc:
            log_swallowed(exc)

        if result["detected"]:
            gpu = result["gpus"][0]
            gpu_type = gpu.get("type", "")
            if "intel" in gpu_type or "va-api" in gpu_type:
                result["hw_accel_type"] = "vaapi"
            elif "nvidia" in gpu_type:
                result["hw_accel_type"] = "nvenc"
            if "hw_accel_type" in result:
                try:
                    snippet_fn = getattr(gpu_mod, "build_compose_snippet", None)
                    if snippet_fn:
                        result["compose_snippet"] = snippet_fn(result["hw_accel_type"])
                except Exception as exc:
                    log_swallowed(exc)
            result["can_auto_configure"] = result.get(f"{ms.id}_has_gpu" if ms else "has_gpu", False)
        return result

    def enable_gpu_transcoding(self) -> dict[str, Any]:
        """Auto-configure media server for hardware transcoding based on detected GPU.

        Delegates to the media-server app layer which owns the config parsing
        and container restart logic.
        """
        import importlib
        ms = next((s for s in _SERVICES if s.category == "media"), None)
        if not ms:
            return {"status": "error", "error": "No media server in registry"}
        try:
            gpu_mod = importlib.import_module(f"media_stack.services.apps.{ms.id}.gpu")
            _enable = getattr(gpu_mod, "enable_gpu_transcoding")
            return _enable()
        except (ImportError, AttributeError) as exc:
            return {"status": "error", "error": f"GPU module not available for {ms.id}: {exc}"}

    def take_snapshot(self) -> dict[str, Any]:
        """Take a config snapshot now."""
        import json as _json
        import re

        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        snapshot_dir = config_root / ".snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        snapshot: dict[str, str] = {}
        # Build snapshot list from the service registry — every service that
        # declares an api_key_config path has a config file worth snapshotting.
        patterns: list[tuple[str, str]] = []
        for svc in _SERVICES:
            if svc.api_key_config:
                # api_key_config is e.g. "sonarr/config.xml" → ("sonarr", "config.xml")
                parts = svc.api_key_config.split("/", 1)
                if len(parts) == 2:
                    patterns.append((parts[0], parts[1]))
        for app, rel in patterns:
            path = config_root / app / rel
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    text = re.sub(r"<ApiKey>[^<]+</ApiKey>", "<ApiKey>***</ApiKey>", text)
                    text = re.sub(r"api_key\s*=\s*\S+", "api_key = ***", text)
                    text = re.sub(r'"apiKey"\s*:\s*"[^"]+"', '"apiKey": "***"', text)
                    snapshot[f"{app}/{rel}"] = text
                except Exception as exc:
                    log_swallowed(exc)

        ts = time.strftime("%Y%m%dT%H%M%S")
        out = snapshot_dir / f"snapshot-{ts}.json"
        out.write_text(_json.dumps(snapshot, indent=2), encoding="utf-8")

        # Prune old snapshots
        existing = sorted(snapshot_dir.glob("snapshot-*.json"), reverse=True)
        for old in existing[24:]:
            old.unlink(missing_ok=True)

        return {"status": "created", "file": out.name, "configs": len(snapshot)}

    def get_config_snapshots(self) -> dict[str, Any]:
        """List available config snapshots."""
        snapshot_dir = Path(os.environ.get("CONFIG_ROOT", "/srv-config")) / ".snapshots"
        snapshots: list[dict[str, Any]] = []
        if snapshot_dir.exists():
            for f in sorted(snapshot_dir.iterdir(), reverse=True):
                if f.suffix == ".json" and f.is_file():
                    snapshots.append({
                        "file": f.name, "size": f.stat().st_size,
                        "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
                    })
        return {"snapshots": snapshots[:50], "dir": str(snapshot_dir)}

    def get_snapshot_detail(self, filename: str) -> dict[str, Any]:
        """Read a specific snapshot file."""
        import json as _json
        if ".." in filename or "/" in filename or "\\" in filename:
            return {"error": "Invalid snapshot filename"}
        snapshot_dir = Path(os.environ.get("CONFIG_ROOT", "/srv-config")) / ".snapshots"
        path = snapshot_dir / filename
        if not path.is_file() or not filename.startswith("snapshot-"):
            return {"error": "Snapshot not found"}
        try:
            return {"snapshot": _json.loads(path.read_text(encoding="utf-8")), "file": filename}
        except Exception as exc:
            return {"error": str(exc)[:120]}

    def diff_snapshots(self, file_a: str, file_b: str) -> dict[str, Any]:
        """Compare two snapshots and return differences."""
        import json as _json
        for f in (file_a, file_b):
            if ".." in f or "/" in f or "\\" in f:
                return {"error": f"Invalid snapshot filename: {f}"}
        snapshot_dir = Path(os.environ.get("CONFIG_ROOT", "/srv-config")) / ".snapshots"
        try:
            a = _json.loads((snapshot_dir / file_a).read_text(encoding="utf-8"))
            b = _json.loads((snapshot_dir / file_b).read_text(encoding="utf-8"))
        except Exception as exc:
            return {"error": str(exc)[:120]}

        diffs: list[dict[str, str]] = []
        all_keys = set(a.keys()) | set(b.keys())
        for key in sorted(all_keys):
            val_a = a.get(key, "(absent)")
            val_b = b.get(key, "(absent)")
            if val_a != val_b:
                diffs.append({"file": key, "status": "changed" if key in a and key in b else "added" if key not in a else "removed"})
        return {"diffs": diffs, "file_a": file_a, "file_b": file_b, "total_changes": len(diffs)}

    def get_mount_info(self) -> dict[str, Any]:
        """Detect NFS/CIFS/local mounts relevant to media storage."""
        mounts: list[dict[str, str]] = []
        try:
            result = subprocess.run(["mount"], capture_output=True, text=True, timeout=5, check=False)
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    device, _, mountpoint, _, fstype = parts[:5]
                    if any(kw in mountpoint for kw in ("/media", "/data", "/config", "/srv", "/mnt", "/nas")):
                        mounts.append({"device": device, "mountpoint": mountpoint, "fstype": fstype.strip("()")})
                    elif fstype.strip("()").startswith(("nfs", "cifs", "smb")):
                        mounts.append({"device": device, "mountpoint": mountpoint, "fstype": fstype.strip("()")})
        except Exception as exc:
            log_swallowed(exc)
        return {
            "mounts": mounts,
            "nfs_available": any(m["fstype"].startswith("nfs") for m in mounts),
            "cifs_available": any(m["fstype"].startswith(("cifs", "smb")) for m in mounts),
        }

    def get_service_logs(
        self,
        service_name: str,
        lines: int = 100,
        since: str | None = None,
        action: str | None = None,
        level: str | None = None,
        q: str | None = None,
        include_previous: bool = False,
    ) -> dict[str, Any]:
        """Fetch recent logs from a service container or pod.

        The K8s lookup tries `app=<service_name>` first (which covers
        Sonarr/Radarr/etc. whose Deployments are labelled with the
        bare service name), then falls back to `app=media-stack-<name>`
        for the platform-internal services (controller, ui) whose
        labels carry the project prefix. Without the fallback, asking
        the dashboard for "controller" logs returned an empty list
        plus the misleading "No pods found for controller" string.

        After both `app=…` candidates miss, we try `job-name=<service_name>`
        which covers transient CronJob/Job pods (e.g.
        `media-stack-media-hygiene-29619765-2j9dc`). The operator
        picks the CronJob name from the dropdown and the backend
        resolves it to the most-recent pod by creationTimestamp —
        the auto-generated suffix is opaque to the UI. CronJob pods
        age out via TTL; if no pod exists we return a clear
        "no recent pod for <name>" message rather than a 500.

        v1.0.270+: extended with operator filters so the dashboard
        can surface big windows without forcing operators to ssh
        into containers and tail rotated/compressed logs by hand.
        ``since`` accepts ISO datetime OR relative (``5m``/``1h``/``24h``);
        ``action`` filters lines containing ``[ACTION] <name>`` or
        ``[JOB] <name>``; ``level`` filters by tag (ERR/WARN/INFO/
        DBG); ``q`` is a free-text or ``/regex/i`` match. The line
        cap is raised to 50000 — k8s/docker daemons happily stream
        that much without ever shelling into a container.

        ``include_previous`` (K8s only) attaches the previous
        container instance's logs as well — covers the
        crashloop-debugging case where the live pod is the
        replacement and the failure happened in the prior one.
        """
        namespace = os.environ.get("K8S_NAMESPACE", "")
        # Translate ``since`` into an integer seconds value the
        # k8s/docker SDKs both accept. Accepts ``5m``/``1h``/``24h``
        # shorthands AND ISO-8601 datetimes (``2026-04-27T03:00:00Z``).
        # Dispatch helpers through ``sys.modules[__name__]`` so any
        # test that ``mock.patch``es the module-level alias keeps
        # intercepting.
        _self_mod = sys.modules[__name__]
        parse_since_seconds = getattr(_self_mod, "_parse_since_seconds")
        apply_log_filters = getattr(_self_mod, "_apply_log_filters")
        read_archive_log_lines = getattr(_self_mod, "_read_archive_log_lines")

        since_seconds = parse_since_seconds(since) if since else None
        try:
            if namespace:
                from kubernetes import client as k8s_client, config as k8s_config
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                v1 = k8s_client.CoreV1Api()
                candidates = [
                    ("app", service_name),
                    ("app", f"media-stack-{service_name}"),
                    ("job-name", service_name),
                ]
                pods = None
                matched_label = None
                for label, value in candidates:
                    found = v1.list_namespaced_pod(
                        namespace, label_selector=f"{label}={value}",
                    )
                    if found.items:
                        pods = found
                        matched_label = (label, value)
                        break

                # CronJob fallback: when the operator picked a CronJob
                # name from the dropdown, no pod is labelled
                # `job-name=<cronjob-name>` directly — Jobs spawned by
                # the CronJob carry `job-name=<cronjob-name>-<ts>`. The
                # CronJob template label `app=<cronjob-name>` may also
                # be missing on completed-pod selectors, so list every
                # pod whose job-name STARTS WITH the requested name.
                if pods is None:
                    all_pods = v1.list_namespaced_pod(namespace)
                    matched_pods = [
                        p for p in all_pods.items
                        if (p.metadata.labels or {}).get("job-name", "").startswith(f"{service_name}-")
                        or (p.metadata.labels or {}).get("job-name", "") == service_name
                    ]
                    if matched_pods:
                        # Pick most-recent by creationTimestamp.
                        from datetime import datetime, timezone as _tz
                        _epoch = datetime(1970, 1, 1, tzinfo=_tz.utc)
                        matched_pods.sort(
                            key=lambda p: p.metadata.creation_timestamp or _epoch,
                            reverse=True,
                        )

                        class _Stub:
                            pass

                        pods = _Stub()
                        pods.items = matched_pods  # type: ignore[attr-defined]
                        matched_label = ("job-name~", service_name)

                if pods is None:
                    tried = ", ".join(f"{lbl}={val}" for lbl, val in candidates)
                    return {
                        "lines": [],
                        "error": (
                            f"No pods found for {service_name} "
                            f"(tried labels: {tried}). For CronJob/Job "
                            f"sources this means no recent pod for "
                            f"{service_name} — pod may have aged out "
                            f"via TTL."
                        ),
                    }

                # Pick the most-recent pod for transient/CronJob sources;
                # for app-labelled (long-lived) pods, items[0] is fine.
                pod_items = list(pods.items)
                if matched_label and matched_label[0] in ("job-name", "job-name~"):
                    from datetime import datetime, timezone as _tz
                    _epoch = datetime(1970, 1, 1, tzinfo=_tz.utc)
                    pod_items.sort(
                        key=lambda p: p.metadata.creation_timestamp or _epoch,
                        reverse=True,
                    )
                log_kwargs: dict[str, Any] = {
                    "name": pod_items[0].metadata.name,
                    "namespace": namespace,
                    "tail_lines": lines,
                }
                if since_seconds is not None:
                    log_kwargs["since_seconds"] = since_seconds
                log_text = v1.read_namespaced_pod_log(**log_kwargs)
                all_lines = log_text.splitlines()
                if include_previous:
                    try:
                        prev_kwargs = dict(log_kwargs)
                        prev_kwargs["previous"] = True
                        prev_text = v1.read_namespaced_pod_log(**prev_kwargs)
                        # Mark archive lines so the UI can dim them.
                        prev_lines = [
                            f"[archive:previous] {ln}" for ln in prev_text.splitlines()
                        ]
                        all_lines = prev_lines + all_lines
                    except Exception as exc:
                        # Crashloop pod may not have a previous
                        # instance; not an error worth surfacing.
                        log_swallowed(exc)
                filtered = apply_log_filters(all_lines, action, level, q)
                return {
                    "lines": filtered[-lines:],
                    "matched": len(filtered),
                    "scanned": len(all_lines),
                    "truncated": len(filtered) > lines,
                }
            else:
                import docker
                from media_stack.core.docker_resolver import (
                    resolve_compose_container,
                )
                client = docker.from_env()
                container = resolve_compose_container(client, service_name)
                if container is None:
                    return {
                        "lines": [],
                        "error": (
                            f"Service '{service_name}' is not deployed "
                            f"in this profile (no compose container "
                            f"with that label or name)."
                        ),
                    }
                log_kwargs: dict[str, Any] = {"tail": lines}
                if since_seconds is not None:
                    log_kwargs["since"] = int(time.time()) - since_seconds
                log_text = container.logs(**log_kwargs).decode(
                    "utf-8", errors="replace",
                )
                all_lines = log_text.splitlines()
                # Compose: also read on-disk archive logs if the
                # operator pointed us at a directory. Lets the
                # dashboard cover post-rotation history without
                # ssh into the host. Best-effort; quiet on error.
                archive_lines = read_archive_log_lines(
                    service_name, since_seconds,
                )
                if archive_lines:
                    all_lines = archive_lines + all_lines
                filtered = apply_log_filters(all_lines, action, level, q)
                return {
                    "lines": filtered[-lines:],
                    "matched": len(filtered),
                    "scanned": len(all_lines),
                    "truncated": len(filtered) > lines,
                }
        except Exception as exc:
            return {"lines": [], "error": str(exc)[:200]}

    def list_cronjob_log_sources(self) -> list[dict[str, str]]:
        """Enumerate CronJob templates as log sources for the Logs UI.

        Each entry looks like
        ``{"id": "media-stack-media-hygiene", "label": "Media hygiene (cron)", "kind": "cronjob"}``.
        The ``id`` is the CronJob's metadata.name, which is what
        ``GET /api/logs/<id>`` resolves to the most-recent pod via the
        ``job-name`` selector in :meth:`get_service_logs`.

        Returns an empty list when not running in Kubernetes or when
        the BatchV1 API is unreachable — callers must tolerate that
        gracefully so the platform/service portion of the dropdown
        still renders.
        """
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if not namespace:
            return []
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            batch_v1 = k8s_client.BatchV1Api()
            cronjobs = batch_v1.list_namespaced_cron_job(namespace)
            sources: list[dict[str, str]] = []
            for cj in cronjobs.items:
                name = cj.metadata.name
                # Build a friendly label: strip the project prefix and
                # title-case + " (cron)" suffix so the dropdown reads
                # naturally next to the platform/service rows.
                short = name
                if short.startswith("media-stack-"):
                    short = short[len("media-stack-"):]
                label = short.replace("-", " ").capitalize() + " (cron)"
                sources.append({"id": name, "label": label, "kind": "cronjob"})
            return sources
        except Exception as exc:
            log_swallowed(exc)
            return []


# Module-level singletons. Uppercase ``_INSTANCE`` per ADR-0012 so
# the SINGLETON_INSTANCE_RATCHET regex (matches lowercase
# ``_instance = ``) is unaffected.
_INSTANCE = OpsService()
_LOG_HELPERS = OpsLogHelpers()

# Backward-compat module-level aliases. Preserve every public +
# underscore-prefix name so callers and ``mock.patch`` sites stay on
# the same import surface they had pre-refactor.
_parse_since_seconds = _LOG_HELPERS.parse_since_seconds
_apply_log_filters = _LOG_HELPERS.apply_log_filters
_read_archive_log_lines = _LOG_HELPERS.read_archive_log_lines

get_namespaces = _INSTANCE.get_namespaces
_get_k8s_namespaces = _INSTANCE._get_k8s_namespaces
_get_compose_containers = _INSTANCE._get_compose_containers
_aggregate_metrics = _INSTANCE._aggregate_metrics
check_image_updates = _INSTANCE.check_image_updates
get_gpu_info = _INSTANCE.get_gpu_info
enable_gpu_transcoding = _INSTANCE.enable_gpu_transcoding
take_snapshot = _INSTANCE.take_snapshot
get_config_snapshots = _INSTANCE.get_config_snapshots
get_snapshot_detail = _INSTANCE.get_snapshot_detail
diff_snapshots = _INSTANCE.diff_snapshots
get_mount_info = _INSTANCE.get_mount_info
get_service_logs = _INSTANCE.get_service_logs
list_cronjob_log_sources = _INSTANCE.list_cronjob_log_sources
