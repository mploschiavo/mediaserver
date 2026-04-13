"""Operations services: namespaces, images, GPU, mounts, snapshots, logs."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("controller_api")

from ._resolve import resolve_config_path


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
            from concurrent.futures import ThreadPoolExecutor, as_completed
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

    @staticmethod
    def _aggregate_metrics(pod_metrics: list[dict[str, str]]) -> dict[str, Any]:
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
                    pass
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
            import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass

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
                import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                pass

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
                    capture_output=True, text=True, timeout=5,
                )
                if nvidia.returncode == 0:
                    for line in nvidia.stdout.strip().splitlines():
                        parts = [p.strip() for p in line.split(",")]
                        result["gpus"].append({"type": "nvidia", "name": parts[0] if parts else "NVIDIA GPU",
                                               "driver": parts[1] if len(parts) > 1 else "",
                                               "memory": parts[2] if len(parts) > 2 else ""})
                        result["detected"] = True
            except FileNotFoundError:
                pass

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
            from .registry import SERVICES
            ms = next((s for s in SERVICES if s.category == "media"), None)
            if ms:
                gpu_mod = importlib.import_module(f"media_stack.services.apps.{ms.id}.gpu")
                check_fn = getattr(gpu_mod, f"check_{ms.id}_gpu", None)
                if check_fn:
                    result.update(check_fn(client))
        except Exception as exc:
            import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass

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
                    import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                    pass
            result["can_auto_configure"] = result.get(f"{ms.id}_has_gpu" if ms else "has_gpu", False)
        return result

    def enable_gpu_transcoding(self) -> dict[str, Any]:
        """Auto-configure media server for hardware transcoding based on detected GPU.

        Delegates to the media-server app layer which owns the config parsing
        and container restart logic.
        """
        import importlib
        from .registry import SERVICES
        ms = next((s for s in SERVICES if s.category == "media"), None)
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
        from .registry import SERVICES as _SERVICES
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
                    import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                    pass

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
            result = subprocess.run(["mount"], capture_output=True, text=True, timeout=5)
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    device, _, mountpoint, _, fstype = parts[:5]
                    if any(kw in mountpoint for kw in ("/media", "/data", "/config", "/srv", "/mnt", "/nas")):
                        mounts.append({"device": device, "mountpoint": mountpoint, "fstype": fstype.strip("()")})
                    elif fstype.strip("()").startswith(("nfs", "cifs", "smb")):
                        mounts.append({"device": device, "mountpoint": mountpoint, "fstype": fstype.strip("()")})
        except Exception as exc:
            import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        return {
            "mounts": mounts,
            "nfs_available": any(m["fstype"].startswith("nfs") for m in mounts),
            "cifs_available": any(m["fstype"].startswith(("cifs", "smb")) for m in mounts),
        }

    def get_service_logs(self, service_name: str, lines: int = 100) -> dict[str, Any]:
        """Fetch recent logs from a service container or pod."""
        namespace = os.environ.get("K8S_NAMESPACE", "")
        try:
            if namespace:
                from kubernetes import client as k8s_client, config as k8s_config
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                v1 = k8s_client.CoreV1Api()
                pods = v1.list_namespaced_pod(namespace, label_selector=f"app={service_name}")
                if not pods.items:
                    return {"lines": [], "error": f"No pods found for {service_name}"}
                log_text = v1.read_namespaced_pod_log(
                    name=pods.items[0].metadata.name, namespace=namespace, tail_lines=lines,
                )
                return {"lines": log_text.splitlines()[-lines:]}
            else:
                import docker
                client = docker.from_env()
                container = client.containers.get(service_name)
                log_text = container.logs(tail=lines).decode("utf-8", errors="replace")
                return {"lines": log_text.splitlines()[-lines:]}
        except Exception as exc:
            return {"lines": [], "error": str(exc)[:80]}


_instance = OpsService()

# Backward compat — callers use module-level functions
get_namespaces = _instance.get_namespaces
_get_k8s_namespaces = _instance._get_k8s_namespaces
_get_compose_containers = _instance._get_compose_containers
_aggregate_metrics = _instance._aggregate_metrics
check_image_updates = _instance.check_image_updates
get_gpu_info = _instance.get_gpu_info
enable_gpu_transcoding = _instance.enable_gpu_transcoding
take_snapshot = _instance.take_snapshot
get_config_snapshots = _instance.get_config_snapshots
get_snapshot_detail = _instance.get_snapshot_detail
diff_snapshots = _instance.diff_snapshots
get_mount_info = _instance.get_mount_info
get_service_logs = _instance.get_service_logs
