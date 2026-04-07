"""Configuration services: profile, routing, backup, env vars, manifests."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from ._resolve import resolve_config_path, resolve_profile_path


def get_profile() -> dict[str, Any]:
    """Read and return the bootstrap profile YAML."""
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if not resolved:
        return {"profile": None, "error": "Profile not found"}
    path = Path(resolved)
    try:
        import yaml
        with open(path) as f:
            profile = yaml.safe_load(f) or {}
        return {"profile": profile, "file": str(path)}
    except ImportError:
        return {"profile_raw": path.read_text(encoding="utf-8"), "file": str(path)}
    except Exception as exc:
        return {"profile": None, "error": str(exc)[:120]}


def save_profile(content: str, reload_config: Callable[[], None] | None = None) -> dict[str, Any]:
    """Save bootstrap profile YAML."""
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if not resolved:
        return {"error": "Profile file not found"}
    path = Path(resolved)
    try:
        path.write_text(content, encoding="utf-8")
        if reload_config:
            reload_config()
        return {"status": "saved", "file": str(path)}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def get_routing() -> dict[str, Any]:
    """Return current routing configuration from profile."""
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    profile_path = Path(resolved) if resolved else None
    routing: dict[str, Any] = {}
    if profile_path and profile_path.is_file():
        try:
            import yaml
            with open(profile_path) as f:
                profile = yaml.safe_load(f) or {}
            routing = profile.get("routing") or {}
        except Exception:
            pass
    return {
        "base_domain": str(routing.get("base_domain", "local")),
        "stack_subdomain": str(routing.get("stack_subdomain", "media-stack")),
        "gateway_host": str(routing.get("gateway_host", "apps.media-stack.local")),
        "gateway_port": int(routing.get("gateway_port", 80)),
        "app_path_prefix": str(routing.get("app_path_prefix", "/app")),
        "strategy": str(routing.get("strategy", "hybrid")),
        "internet_exposed": bool(routing.get("internet_exposed", False)),
        "direct_hosts": dict(routing.get("direct_hosts") or {}),
    }


def update_routing(updates: dict[str, Any], action_trigger: Callable | None = None) -> dict[str, Any]:
    """Update routing config in profile YAML and trigger regeneration."""
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if not resolved:
        return {"error": "Profile file not found"}
    profile_path = Path(resolved)
    try:
        import yaml
        with open(profile_path) as f:
            profile = yaml.safe_load(f) or {}
        routing = profile.setdefault("routing", {})
        allowed_keys = {"base_domain", "stack_subdomain", "gateway_host", "gateway_port", "app_path_prefix", "strategy", "internet_exposed"}
        changed = []
        for key, value in updates.items():
            if key in allowed_keys and str(routing.get(key, "")) != str(value):
                routing[key] = value
                changed.append(key)
        # Only auto-derive gateway_host if it wasn't explicitly set
        if ("stack_subdomain" in changed or "base_domain" in changed) and "gateway_host" not in changed:
            sub = routing.get("stack_subdomain", "media-stack")
            dom = routing.get("base_domain", "local")
            # Preserve existing prefix (e.g. "apps" from "apps.old.local")
            old_host = str(routing.get("gateway_host", ""))
            prefix = old_host.split(".")[0] if old_host and "." in old_host else "apps"
            routing["gateway_host"] = f"{prefix}.{sub}.{dom}"
            changed.append("gateway_host")
        if not changed:
            return {"status": "no_changes", "routing": routing}
        with open(profile_path, "w") as f:
            yaml.dump(profile, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        if action_trigger:
            action_trigger("envoy-config", {})
        return {"status": "updated", "changed": changed, "routing": routing}
    except Exception as exc:
        return {"error": str(exc)[:200]}


def get_env() -> dict[str, Any]:
    """Return runtime environment information."""
    import platform
    import socket

    namespace = os.environ.get("K8S_NAMESPACE", "")
    profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
    profile_name = ""
    resolved = resolve_profile_path(profile_file)
    if resolved:
        profile_name = Path(resolved).name

    node_ip = os.environ.get("NODE_IP", "")
    if not node_ip:
        try:
            node_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            node_ip = ""

    # Multi-node K8s: discover all node IPs
    node_ips: list[str] = [node_ip] if node_ip else []
    if namespace:
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            v1 = k8s_client.CoreV1Api()
            nodes = v1.list_node()
            node_ips = []
            for node in nodes.items:
                for addr in (node.status.addresses or []):
                    if addr.type == "InternalIP":
                        node_ips.append(addr.address)
                        break
        except Exception:
            pass

    return {
        "namespace": namespace,
        "profile_name": profile_name,
        "node_ip": node_ip,
        "node_ips": node_ips,
        "node_count": len(node_ips),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "runtime": "kubernetes" if namespace else "compose",
    }


def get_backup(state: Any) -> bytes:
    """Create a JSON backup of all discoverable config."""
    backup: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "env": get_env(),
        "state": state.to_dict() if hasattr(state, "to_dict") else {},
    }
    resolved_profile = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if resolved_profile:
        backup["profile_raw"] = Path(resolved_profile).read_text(encoding="utf-8", errors="replace")
    return json.dumps(backup, indent=2, default=str).encode("utf-8")


def get_envvars() -> dict[str, str]:
    """Return relevant environment variables."""
    relevant_prefixes = (
        "BOOTSTRAP_", "STACK_", "K8S_", "SONARR_", "RADARR_", "LIDARR_",
        "READARR_", "PROWLARR_", "BAZARR_", "SABNZBD_", "JELLYFIN_",
        "JELLYSEERR_", "TAUTULLI_", "PUID", "PGID", "TZ",
    )
    return {
        k: v for k, v in sorted(os.environ.items())
        if any(k.startswith(p) for p in relevant_prefixes)
    }


def set_envvar(key: str, value: str) -> dict[str, Any]:
    """Set an environment variable."""
    os.environ[key] = value
    return {"status": "set", "key": key, "value": value}


def get_manifests() -> dict[str, Any]:
    """Return the compose file, bootstrap config, or kustomization content."""
    namespace = os.environ.get("K8S_NAMESPACE", "")

    # K8s: try to get kustomization or deployment spec
    if namespace:
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            apps_v1 = k8s_client.AppsV1Api()
            deps = apps_v1.list_namespaced_deployment(namespace)
            services = [{"name": d.metadata.name, "image": d.spec.template.spec.containers[0].image if d.spec.template.spec.containers else ""} for d in deps.items]
            return {"type": "kubernetes", "namespace": namespace, "deployments": len(services), "services": services}
        except Exception as exc:
            return {"type": "kubernetes", "error": str(exc)[:80]}

    # Compose: try to find compose file
    compose_file = os.environ.get("COMPOSE_FILE", "")
    if not compose_file:
        for candidate in ["/compose/docker-compose.yml", "./docker-compose.yml"]:
            if Path(candidate).is_file():
                compose_file = candidate
                break
    if compose_file and Path(compose_file).is_file():
        return {"type": "compose", "file": compose_file, "content": Path(compose_file).read_text(encoding="utf-8", errors="replace")}

    # Fallback: show bootstrap config JSON (always available in image)
    config_path = resolve_config_path()
    if config_path:
        try:
            cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
            # Show a summary, not the full 60KB config
            summary = {
                "services": list((cfg.get("services") or {}).keys()) if isinstance(cfg.get("services"), dict) else [],
                "disk_guardrails": cfg.get("disk_guardrails", {}).get("enabled", False),
                "preflight_handlers": [h.get("name") for h in cfg.get("container_preflight_handlers", [])],
                "post_handlers": [h.get("name") for h in cfg.get("container_post_bootstrap_handlers", [])],
            }
            return {"type": "bootstrap-config", "file": config_path, "content": json.dumps(summary, indent=2)}
        except Exception:
            pass

    # Also try listing running containers as a manifest equivalent
    try:
        import docker
        client = docker.from_env()
        containers = [{"name": c.name, "image": c.image.tags[0] if c.image.tags else str(c.image.short_id), "status": c.status} for c in client.containers.list()]
        return {"type": "compose-runtime", "content": json.dumps(containers, indent=2), "note": "Compose file not mounted. Showing running containers."}
    except Exception:
        pass

    return {"type": "unknown", "content": None, "error": "No manifest found. Mount compose file or use K8s."}
