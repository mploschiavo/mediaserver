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
                results.append({
                    "name": c.name, "image": image, "tag": tag,
                    "started_at": started[:19].replace("T", " ") if started else "",
                    "image_created": created[:19].replace("T", " ") if created else "",
                })
        except Exception as exc:
            return {"error": str(exc)[:80]}
    pinned = sum(1 for r in results if r["tag"] not in ("latest",))
    return {"images": results, "total": len(results), "pinned": pinned}


def get_gpu_info() -> dict[str, Any]:
    """Detect GPU hardware for transcoding configuration."""
    result: dict[str, Any] = {"detected": False, "gpus": [], "jellyfin_configured": False}

    render_devices = list(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else []
    if render_devices:
        gpu_info: dict[str, Any] = {"type": "intel", "driver": "va-api", "devices": [str(d) for d in render_devices]}
        try:
            lspci = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
            for line in lspci.stdout.splitlines():
                if "VGA" in line or "Display" in line:
                    gpu_info["name"] = line.split(":", 2)[-1].strip() if ":" in line else line
                    break
        except Exception:
            gpu_info["name"] = "Intel integrated (VA-API)"
        result["gpus"].append(gpu_info)
        result["detected"] = True

    try:
        nvidia = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if nvidia.returncode == 0:
            for line in nvidia.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                result["gpus"].append({
                    "type": "nvidia", "name": parts[0] if parts else "NVIDIA GPU",
                    "driver": parts[1] if len(parts) > 1 else "",
                    "memory": parts[2] if len(parts) > 2 else "",
                })
                result["detected"] = True
    except FileNotFoundError:
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
        if gpu["type"] == "intel":
            result["compose_snippet"] = (
                "# Add to jellyfin service in docker-compose.yml:\n"
                "    devices:\n      - /dev/dri:/dev/dri\n"
                "    group_add:\n      - \"44\"   # video\n      - \"109\"  # render"
            )
        elif gpu["type"] == "nvidia":
            result["compose_snippet"] = (
                "# Use the jellyfin-nvidia profile:\n# docker compose --profile nvidia up -d\n"
                "# Or add to jellyfin service:\n    runtime: nvidia\n    environment:\n"
                "      - NVIDIA_VISIBLE_DEVICES=all\n      - NVIDIA_DRIVER_CAPABILITIES=compute,video,utility"
            )
    return result


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
