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
    """Return current routing configuration — persisted overrides take precedence."""
    import yaml

    routing: dict[str, Any] = {}

    # 1. Load base from profile YAML
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if resolved:
        try:
            with open(resolved) as f:
                profile = yaml.safe_load(f) or {}
            routing = dict(profile.get("routing") or {})
        except Exception:
            pass

    # 2. Overlay persisted runtime overrides (from POST /api/routing)
    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    overrides_path = config_root / ".controller" / "routing-overrides.yaml"
    if overrides_path.is_file():
        try:
            overrides = yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}
            routing.update(overrides.get("routing") or {})
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
        # Sync gateway_host <-> subdomain/domain in both directions
        if ("stack_subdomain" in changed or "base_domain" in changed) and "gateway_host" not in changed:
            # Derive gateway_host from subdomain + domain
            sub = routing.get("stack_subdomain", "media-stack")
            dom = routing.get("base_domain", "local")
            old_host = str(routing.get("gateway_host", ""))
            prefix = old_host.split(".")[0] if old_host and "." in old_host else "apps"
            routing["gateway_host"] = f"{prefix}.{sub}.{dom}"
            changed.append("gateway_host")
        elif "gateway_host" in changed and "stack_subdomain" not in changed and "base_domain" not in changed:
            # Derive subdomain + domain from gateway_host
            parts = str(routing["gateway_host"]).split(".")
            if len(parts) >= 3:
                routing["stack_subdomain"] = parts[1]
                routing["base_domain"] = ".".join(parts[2:])
                if "stack_subdomain" not in changed:
                    changed.append("stack_subdomain")
                if "base_domain" not in changed:
                    changed.append("base_domain")
        if not changed:
            return {"status": "no_changes", "routing": routing}
        # Persist to writable config root (survives container restarts)
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        overrides_path = config_root / ".controller" / "routing-overrides.yaml"
        overrides_path.parent.mkdir(parents=True, exist_ok=True)
        with open(overrides_path, "w") as f:
            yaml.dump({"routing": routing}, f, default_flow_style=False, sort_keys=False)
        # Also try to update the profile source (may be read-only)
        try:
            with open(profile_path, "w") as f:
                yaml.dump(profile, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        except OSError:
            pass
        if action_trigger:
            action_trigger("envoy-config", {})
        return {
            "status": "updated",
            "persisted_to": str(overrides_path),
            "changed": changed,
            "routing": routing,
        }
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
    """Create a JSON backup of all discoverable config and service state."""
    backup: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "version": "2",
        "env": get_env(),
        "state": state.to_dict() if hasattr(state, "to_dict") else {},
    }

    # Profile YAML
    resolved_profile = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if resolved_profile:
        backup["profile_raw"] = Path(resolved_profile).read_text(encoding="utf-8", errors="replace")

    # Service configs from config root — registry-driven paths
    from .registry import SERVICES as _backup_svcs
    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    service_configs: dict[str, str] = {}
    if config_root.is_dir():
        # Collect config file paths from the service registry
        config_files: list[str] = []
        for svc in _backup_svcs:
            if svc.api_key_config:
                config_files.append(svc.api_key_config)
            if svc.password_config:
                config_files.append(svc.password_config)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_configs: list[str] = []
        for cf in config_files:
            if cf not in seen:
                seen.add(cf)
                unique_configs.append(cf)
        for rel_path in unique_configs:
            full_path = config_root / rel_path
            if full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    if len(content) < 100_000:  # Skip huge files
                        service_configs[rel_path] = content
                except Exception:
                    pass
    if service_configs:
        backup["service_configs"] = service_configs

    # API keys — full values for restore, masked preview for display
    api_keys: dict[str, str] = {}
    api_keys_masked: dict[str, str] = {}
    for key, value in sorted(os.environ.items()):
        if key.endswith("_API_KEY") and value:
            api_keys[key] = value
            api_keys_masked[key] = value[:8] + "..." if len(value) > 8 else value
    if api_keys:
        backup["api_keys"] = api_keys
        backup["api_keys_masked"] = api_keys_masked

    # Known config paths from registry (for restore validation)
    valid_paths: list[str] = []
    for svc in _backup_svcs:
        if svc.api_key_config:
            valid_paths.append(svc.api_key_config)
        if svc.password_config:
            valid_paths.append(svc.password_config)
    backup["valid_config_paths"] = sorted(set(valid_paths))

    return json.dumps(backup, indent=2, default=str).encode("utf-8")


def restore_backup(backup: dict[str, Any], state: Any = None) -> dict[str, Any]:
    """Restore service configs from a backup JSON payload.

    Creates a pre-restore backup, validates paths against the service
    registry, restores API keys to env vars, and rolls back on failure.
    """
    # Validate backup version
    version = str(backup.get("version", ""))
    if version not in ("1", "2"):
        return {"status": "error", "error": f"unsupported backup version: {version!r}"}

    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    restored: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    # Build set of valid config paths from the registry
    from .registry import SERVICES as _restore_svcs
    valid_paths: set[str] = set()
    for svc in _restore_svcs:
        if svc.api_key_config:
            valid_paths.add(svc.api_key_config)
        if svc.password_config:
            valid_paths.add(svc.password_config)

    # Pre-restore backup — save current state before overwriting
    pre_restore: dict[str, str] = {}
    service_configs = backup.get("service_configs", {})
    if not isinstance(service_configs, dict):
        return {"status": "error", "error": "service_configs must be an object"}

    for rel_path in service_configs:
        existing = config_root / rel_path
        if existing.is_file():
            try:
                pre_restore[rel_path] = existing.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    # Restore service configs
    for rel_path, content in service_configs.items():
        if ".." in rel_path or rel_path.startswith("/"):
            errors.append(f"skipped unsafe path: {rel_path}")
            continue
        if valid_paths and rel_path not in valid_paths:
            skipped.append(rel_path)
            continue
        target = config_root / rel_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            restored.append(rel_path)
        except Exception as exc:
            errors.append(f"{rel_path}: {exc}")

    # Rollback on critical failure (>50% errors)
    if errors and len(errors) > len(restored):
        rollback_ok = 0
        for rel_path, content in pre_restore.items():
            try:
                (config_root / rel_path).write_text(content, encoding="utf-8")
                rollback_ok += 1
            except Exception:
                pass
        return {
            "status": "rolled_back",
            "errors": errors,
            "rollback_count": rollback_ok,
            "note": "More errors than successes — rolled back to pre-restore state",
        }

    # Restore API keys to environment
    api_keys = backup.get("api_keys", {})
    keys_restored: list[str] = []
    if isinstance(api_keys, dict):
        for key, value in api_keys.items():
            if key.endswith("_API_KEY") and isinstance(value, str) and value and "..." not in value:
                os.environ[key] = value
                keys_restored.append(key)

    return {
        "status": "ok" if not errors else "partial",
        "restored": restored,
        "skipped": skipped,
        "keys_restored": keys_restored,
        "errors": errors,
        "pre_restore_count": len(pre_restore),
        "note": "Restart services to apply restored configs",
    }


def get_envvars() -> dict[str, str]:
    """Return relevant environment variables — prefixes derived from registry."""
    from .registry import SERVICES as _env_svcs
    # Platform prefixes that are always relevant
    _platform = ("BOOTSTRAP_", "STACK_", "K8S_", "CONTROLLER_", "PUID", "PGID", "TZ")
    # Service-derived prefixes from api_key_env (e.g. SONARR_API_KEY → SONARR_)
    _svc = {e.api_key_env.split("_")[0] + "_" for e in _env_svcs if e.api_key_env}
    relevant_prefixes = set(_platform) | _svc
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
                "post_handlers": [h.get("name") for h in cfg.get("container_post_setup_handlers", [])],
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
