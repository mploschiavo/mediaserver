"""Operations services: namespaces, images, GPU, mounts, snapshots, logs."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ._resolve import resolve_config_path


def get_namespaces() -> dict[str, Any]:
    """List K8s namespaces with pod details, or compose container info."""
    namespace = os.environ.get("K8S_NAMESPACE", "")
    if namespace:
        return _get_k8s_namespaces(namespace)
    return _get_compose_containers()


def _get_k8s_namespaces(namespace: str) -> dict[str, Any]:
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
        except Exception:
            pass

        return {"namespaces": ns_info, "services": services, "pod_metrics": pod_metrics}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def _get_compose_containers() -> dict[str, Any]:
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

        return {"namespaces": ns_info, "services": services, "pod_metrics": pod_metrics}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def check_image_updates() -> dict[str, Any]:
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
    pinned = sum(1 for r in results if r["tag"] not in ("latest",))
    return {"images": results, "total": len(results), "pinned": pinned}


def get_gpu_info() -> dict[str, Any]:
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
    except Exception:
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
        except Exception:
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
            result["note"] = "On Kubernetes, request GPU via resource limits: nvidia.com/gpu: 1 in the Jellyfin pod spec."
        else:
            result["note"] = "No containers have GPU devices mounted. To enable, update docker-compose.yml and redeploy."

    # Check if Jellyfin container has GPU passthrough
    try:
        import docker
        client = docker.from_env()
        jf = client.containers.get("jellyfin")
        devices = jf.attrs.get("HostConfig", {}).get("Devices") or []
        groups = jf.attrs.get("HostConfig", {}).get("GroupAdd") or []
        if any("/dev/dri" in str(d) for d in devices):
            result["jellyfin_has_gpu"] = True
        elif any("dri" in str(d) for d in devices):
            result["jellyfin_has_gpu"] = True
        # Check runtime for nvidia
        runtime = jf.attrs.get("HostConfig", {}).get("Runtime", "")
        if runtime == "nvidia":
            result["jellyfin_has_gpu"] = True
    except Exception:
        pass

    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    jf_system = Path(config_root) / "jellyfin" / "config" / "system.xml"
    if jf_system.is_file():
        try:
            import re
            text = jf_system.read_text(encoding="utf-8")
            result["jellyfin_configured"] = "EnableHardwareDecoding" in text and ">true<" in text.lower()
            m = re.search(r"<HardwareAccelerationType>(\w+)</HardwareAccelerationType>", text)
            if m:
                result["jellyfin_hw_type"] = m.group(1)
        except Exception:
            pass

    if result["detected"]:
        gpu = result["gpus"][0]
        gpu_type = gpu.get("type", "")
        if "intel" in gpu_type or "va-api" in gpu_type:
            result["hw_accel_type"] = "vaapi"
            result["compose_snippet"] = (
                "# Add to jellyfin service in docker-compose.yml:\n"
                "    devices:\n      - /dev/dri:/dev/dri\n"
                "    group_add:\n      - \"44\"   # video\n      - \"109\"  # render"
            )
        elif "nvidia" in gpu_type:
            result["hw_accel_type"] = "nvenc"
            result["compose_snippet"] = (
                "# Use the jellyfin-nvidia profile:\n# docker compose --profile nvidia up -d\n"
                "# Or add to jellyfin service:\n    runtime: nvidia\n    environment:\n"
                "      - NVIDIA_VISIBLE_DEVICES=all\n      - NVIDIA_DRIVER_CAPABILITIES=compute,video,utility"
            )
        result["can_auto_configure"] = result.get("jellyfin_has_gpu", False)
    return result


def enable_gpu_transcoding() -> dict[str, Any]:
    """Auto-configure Jellyfin for hardware transcoding based on detected GPU.

    Creates a backup of system.xml before modifying. If Jellyfin fails to
    restart, automatically rolls back to the backup.
    """
    gpu_info = get_gpu_info()

    if not gpu_info.get("detected"):
        return {"status": "error", "error": "No GPU detected. Mount GPU device to container first."}

    if not gpu_info.get("jellyfin_has_gpu"):
        return {"status": "error", "error": "Jellyfin container does not have GPU devices mounted.",
                "compose_snippet": gpu_info.get("compose_snippet", "")}

    hw_type = gpu_info.get("hw_accel_type", "vaapi")
    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    system_xml = config_root / "jellyfin" / "config" / "system.xml"

    if not system_xml.is_file():
        return {"status": "error", "error": "Jellyfin system.xml not found. Start Jellyfin first."}

    import re

    original_text = system_xml.read_text(encoding="utf-8")

    if "</ServerConfiguration>" not in original_text:
        return {"status": "error", "error": "system.xml does not contain expected XML structure."}

    # Backup before modifying
    backup_path = system_xml.with_suffix(".xml.gpu-backup")
    try:
        backup_path.write_text(original_text, encoding="utf-8")
    except Exception as exc:
        return {"status": "error", "error": f"Failed to create backup: {exc}"}

    text = original_text
    changes: list[str] = []

    def _set_xml(content: str, tag: str, value: str) -> str:
        pattern = rf"<{tag}>.*?</{tag}>"
        replacement = f"<{tag}>{value}</{tag}>"
        if re.search(pattern, content):
            return re.sub(pattern, replacement, content)
        return content.replace("</ServerConfiguration>",
                               f"  {replacement}\n</ServerConfiguration>")

    text = _set_xml(text, "HardwareAccelerationType", hw_type)
    changes.append(f"HardwareAccelerationType={hw_type}")

    for tag in ("EnableHardwareDecoding", "EnableHardwareEncoding"):
        text = _set_xml(text, tag, "true")
        changes.append(f"{tag}=true")

    if hw_type == "vaapi":
        text = _set_xml(text, "VaapiDevice", "/dev/dri/renderD128")
        changes.append("VaapiDevice=/dev/dri/renderD128")
        for codec in ("EnableTonemapping", "EnableVppTonemapping"):
            text = _set_xml(text, codec, "true")
            changes.append(f"{codec}=true")

    try:
        system_xml.write_text(text, encoding="utf-8")
    except Exception as exc:
        return {"status": "error", "error": f"Failed to write system.xml: {exc}"}

    # Restart Jellyfin and verify it comes back healthy
    restart_note = ""
    rollback = False
    try:
        import docker
        client = docker.from_env()
        jf = client.containers.get("jellyfin")
        jf.restart(timeout=30)
        # Wait up to 30s for Jellyfin to come back healthy
        import time as _time
        for _ in range(15):
            _time.sleep(2)
            jf.reload()
            status = jf.status
            if status == "running":
                health = (jf.attrs.get("State") or {}).get("Health", {}).get("Status", "")
                if health in ("healthy", ""):
                    restart_note = "Jellyfin restarted and running."
                    break
            elif status in ("exited", "dead"):
                rollback = True
                restart_note = "Jellyfin failed to start after config change."
                break
        else:
            restart_note = "Jellyfin restarted (health check pending)."
    except Exception:
        restart_note = "Restart Jellyfin manually to apply changes."

    if rollback:
        # Roll back to backup
        try:
            system_xml.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
            import docker
            client = docker.from_env()
            client.containers.get("jellyfin").restart(timeout=30)
            restart_note += " Rolled back to previous config and restarted."
        except Exception:
            restart_note += " Rollback written but manual restart needed."
        return {
            "status": "error",
            "error": "Jellyfin crashed after enabling GPU transcoding. Config rolled back.",
            "hw_accel_type": hw_type,
            "changes": changes,
            "note": restart_note,
            "backup": str(backup_path),
        }

    return {
        "status": "ok",
        "hw_accel_type": hw_type,
        "changes": changes,
        "note": restart_note,
        "backup": str(backup_path),
    }


def take_snapshot() -> dict[str, Any]:
    """Take a config snapshot now."""
    import json as _json
    import re

    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    snapshot_dir = config_root / ".snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    snapshot: dict[str, str] = {}
    patterns = [
        ("sonarr", "config.xml"), ("radarr", "config.xml"), ("lidarr", "config.xml"),
        ("readarr", "config.xml"), ("prowlarr", "config.xml"),
        ("bazarr", "config/config.yaml"), ("sabnzbd", "sabnzbd.ini"),
        ("jellyseerr", "settings.json"), ("homepage", "services.yaml"),
        ("tautulli", "config.ini"),
    ]
    for app, rel in patterns:
        path = config_root / app / rel
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                text = re.sub(r"<ApiKey>[^<]+</ApiKey>", "<ApiKey>***</ApiKey>", text)
                text = re.sub(r"api_key\s*=\s*\S+", "api_key = ***", text)
                text = re.sub(r'"apiKey"\s*:\s*"[^"]+"', '"apiKey": "***"', text)
                snapshot[f"{app}/{rel}"] = text
            except Exception:
                pass

    ts = time.strftime("%Y%m%dT%H%M%S")
    out = snapshot_dir / f"snapshot-{ts}.json"
    out.write_text(_json.dumps(snapshot, indent=2), encoding="utf-8")

    # Prune old snapshots
    existing = sorted(snapshot_dir.glob("snapshot-*.json"), reverse=True)
    for old in existing[24:]:
        old.unlink(missing_ok=True)

    return {"status": "created", "file": out.name, "configs": len(snapshot)}


def get_config_snapshots() -> dict[str, Any]:
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


def get_snapshot_detail(filename: str) -> dict[str, Any]:
    """Read a specific snapshot file."""
    import json as _json
    snapshot_dir = Path(os.environ.get("CONFIG_ROOT", "/srv-config")) / ".snapshots"
    path = snapshot_dir / filename
    if not path.is_file() or not filename.startswith("snapshot-"):
        return {"error": "Snapshot not found"}
    try:
        return {"snapshot": _json.loads(path.read_text(encoding="utf-8")), "file": filename}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def diff_snapshots(file_a: str, file_b: str) -> dict[str, Any]:
    """Compare two snapshots and return differences."""
    import json as _json
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


def get_mount_info() -> dict[str, Any]:
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
    except Exception:
        pass
    return {
        "mounts": mounts,
        "nfs_available": any(m["fstype"].startswith("nfs") for m in mounts),
        "cifs_available": any(m["fstype"].startswith(("cifs", "smb")) for m in mounts),
    }


def get_service_logs(service_name: str, lines: int = 100) -> dict[str, Any]:
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
