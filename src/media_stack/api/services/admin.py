"""Admin services: API key rotation, password reset, service restart.

All operations are driven by the service registry — no hardcoded app
names or paths. To support a new service, add its ServiceDef to registry.py.
"""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path
from typing import Any

from .registry import (
    SERVICES, SERVICE_MAP,
    get_services_with_api_keys, get_services_with_password_api, get_services_with_password_config,
    read_api_key_from_file, read_api_key_via_http,
)


# ---------------------------------------------------------------------------
# Key reading/writing — delegated to shared key_formats module.
# ---------------------------------------------------------------------------

from .key_formats import READERS as _KEY_READERS, WRITERS as _KEY_WRITERS
import logging


# ---------------------------------------------------------------------------
# App-specific admin operations — dispatched to services/apps/<id>/admin_ops.py
# ---------------------------------------------------------------------------



class AdminService:
    """Admin operations: key rotation, password reset, service restart, K8s secrets."""

    def is_media_server_reset_path(self, path: str) -> bool:
        """Return True if the request path is a legacy media-server reset endpoint."""
        # Dynamically build from registry — any media-category service gets /api/{id}/reset
        return any(
            path == f"/api/{s.id}/reset" for s in SERVICES if s.category == "media"
        )

    def jellyfin_hard_reset(self, username: str, password: str) -> dict[str, Any]:
        """Hard-reset media server credentials — delegates to the app layer."""
        # Find the media server from the registry
        ms = next((s for s in SERVICES if s.category == "media"), None)
        if not ms:
            return {"status": "error", "error": "No media server in service registry"}
        ops = _load_app_admin_ops(ms.id)
        if ops and hasattr(ops, "hard_reset"):
            return ops.hard_reset(username, password)
        return {"status": "error", "error": f"No hard_reset handler for {ms.id}"}

    def hard_reset_service(self, service_id: str, options: dict) -> dict[str, Any]:
        """Hard-reset a service: restart container, re-discover API key, re-run health check.

        For Jellyfin/media-server services, delegates to jellyfin_hard_reset().
        For all others: restart + re-discover key if applicable.
        """
        svc = SERVICE_MAP.get(service_id)
        if not svc:
            return {"status": "error", "error": f"Unknown service '{service_id}'"}

        # Media server or any service with app-layer hard_reset: delegate
        ops = _load_app_admin_ops(service_id)
        if ops and hasattr(ops, "hard_reset"):
            username = options.get("username", os.environ.get("STACK_ADMIN_USERNAME", "admin"))
            password = options.get("password", os.environ.get("STACK_ADMIN_PASSWORD", ""))
            return ops.hard_reset(username, password)

        restarted = False
        key_discovered = False
        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")

        # 1. Restart the container
        try:
            result = self.restart_service(service_id)
            restarted = result.get("status") == "restarted"
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass

        # 2. Wait for health endpoint
        if restarted and svc.host and svc.port:
            import time
            health_url = f"http://{svc.host}:{svc.port}{svc.health_path}"
            for _ in range(15):
                time.sleep(2)
                try:
                    req = urllib.request.Request(health_url)
                    urllib.request.urlopen(req, timeout=5)
                    break
                except Exception as exc:
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                    continue

        # 3. Re-discover API key if service has one
        if svc.api_key_env:
            try:
                key = read_api_key_from_file(service_id, config_root)
                source = "config_file"
                if not key:
                    key = read_api_key_via_http(service_id)
                    source = "http"
                if key:
                    os.environ[svc.api_key_env] = key
                    self.persist_keys_to_secret({svc.api_key_env: key})
                    key_discovered = True
            except Exception as exc:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                pass

        return {
            "status": "reset",
            "service": service_id,
            "restarted": restarted,
            "key_discovered": key_discovered,
        }

    def _discover_jellyfin_admin_user_id(self, base_url: str, api_key: str, preferred_name: str = "admin") -> str:
        """Find the admin user ID in Jellyfin."""
        try:
            req = urllib.request.Request(
                f"{base_url}/Users?api_key={api_key}",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                users = json.loads(resp.read())
            if not users:
                return ""
            # Prefer exact name match, then first admin
            for u in users:
                if str(u.get("Name", "")).strip().lower() == preferred_name.lower():
                    return str(u.get("Id", ""))
            for u in users:
                if u.get("Policy", {}).get("IsAdministrator"):
                    return str(u.get("Id", ""))
            return str(users[0].get("Id", ""))
        except Exception:
            return ""

    # -----------------------------------------------------------------------
    # API key rotation — registry-driven
    # -----------------------------------------------------------------------

    def rotate_keys(self, target_services: list[str] | None = None) -> dict[str, Any]:
        """Regenerate API keys for services that have them.

        If *target_services* is provided, only rotate keys for those service IDs.
        """
        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        rotated: dict[str, str] = {}
        errors: list[str] = []
        file_based_services: list[str] = []
        _filter = set(target_services) if target_services else None

        for svc in get_services_with_api_keys():
            if not svc.api_key_config or not svc.api_key_format:
                continue
            if _filter is not None and svc.id not in _filter:
                continue

            # Jellyfin: rotate via API, not file
            if svc.api_key_format == "sqlite":
                try:
                    old_key = _read_key_sqlite(Path(config_root) / svc.api_key_config)
                    if old_key:
                        req = urllib.request.Request(
                            f"http://{svc.host}:{svc.port}/Auth/Keys?app=media-stack-controller",
                            method="POST", headers={"X-Emby-Token": old_key},
                        )
                        urllib.request.urlopen(req, timeout=5)
                        new_key = _read_key_sqlite(Path(config_root) / svc.api_key_config)
                        if new_key and new_key != old_key:
                            os.environ[svc.api_key_env] = new_key
                            rotated[svc.api_key_env] = new_key
                except Exception as exc:
                    errors.append(f"{svc.id}: {exc}")
                continue

            # File-based rotation
            cfg_path = Path(config_root) / svc.api_key_config
            if not cfg_path.is_file():
                continue

            writer = _KEY_WRITERS.get(svc.api_key_format)
            if not writer:
                continue

            try:
                new_key = uuid.uuid4().hex
                if svc.api_key_format == "json":
                    new_key = base64.b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes).decode("utf-8")
                writer(cfg_path, new_key)
                os.environ[svc.api_key_env] = new_key
                rotated[svc.api_key_env] = new_key
                file_based_services.append(svc.id)
            except Exception as exc:
                errors.append(f"{svc.id}: {exc}")

        self.persist_keys_to_secret(rotated)

        # Auto-restart file-based services
        restarted = []
        for svc_id in file_based_services:
            try:
                self.restart_service(svc_id)
                restarted.append(svc_id)
            except Exception as exc:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                pass

        return {"status": "rotated", "keys": list(rotated.keys()), "errors": errors, "restarted": restarted}

    # -----------------------------------------------------------------------
    # Password reset — registry-driven
    # -----------------------------------------------------------------------

    def reset_password(self, new_password: str, target_services: list[str] | None = None) -> dict[str, Any]:
        """Reset admin password across services that support it.

        If *target_services* is provided, only reset passwords for those service IDs.
        """
        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        old_password = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
        username = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        updated: list[str] = []
        errors: list[str] = []

        _filter = set(target_services) if target_services else None

        # 1. Services with app-layer admin_ops.reset_password() — dynamic dispatch
        handled_ids: set[str] = set()
        for svc in SERVICES:
            if _filter is not None and svc.id not in _filter:
                continue
            ops = _load_app_admin_ops(svc.id)
            if ops and hasattr(ops, "reset_password"):
                ok, err = ops.reset_password(svc, username, old_password, new_password, config_root)
                if ok:
                    updated.append(svc.id)
                elif err:
                    errors.append(f"{svc.id}: {err}")
                handled_ids.add(svc.id)

        # 2. Arr apps — registry-driven via password_api_path
        for svc in get_services_with_password_api():
            if svc.id in handled_ids:
                continue
            if _filter is not None and svc.id not in _filter:
                continue
            try:
                api_key = os.environ.get(svc.api_key_env, "") or self._read_key(svc, config_root)
                if not api_key:
                    errors.append(f"{svc.id}: no API key available")
                    continue
                req = urllib.request.Request(
                    f"http://{svc.host}:{svc.port}{svc.password_api_path}",
                    headers={"X-Api-Key": api_key, "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    cfg = json.loads(resp.read())
                cfg["username"] = username
                cfg["password"] = new_password
                cfg["passwordConfirmation"] = new_password
                # Enable Forms auth if currently disabled — prevents "disabled" login status
                if str(cfg.get("authenticationMethod", "")).lower() in ("none", ""):
                    cfg["authenticationMethod"] = "forms"
                put_req = urllib.request.Request(
                    f"http://{svc.host}:{svc.port}{svc.password_api_path}",
                    data=json.dumps(cfg).encode(), method="PUT",
                    headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
                )
                urllib.request.urlopen(put_req, timeout=5)
                updated.append(svc.id)
            except Exception as exc:
                errors.append(f"{svc.id}: {exc}")

        # (Bazarr and other services with custom password APIs are now handled
        #  via app-layer admin_ops.py dispatch in step 1 above.)

        # 5. Config-file-based password services — registry-driven
        for svc in get_services_with_password_config():
            if svc.id in updated or svc.id in handled_ids:
                continue  # Already handled
            if _filter is not None and svc.id not in _filter:
                continue
            cfg_path = Path(config_root) / svc.password_config
            if not cfg_path.is_file():
                continue
            try:
                if svc.password_config.endswith(".yaml"):
                    import yaml
                    with open(cfg_path) as f:
                        data = yaml.safe_load(f) or {}
                    data.setdefault("auth", {})["username"] = username
                    data["auth"]["password"] = new_password
                    data["auth"]["type"] = "basic"
                    with open(cfg_path, "w") as f:
                        yaml.dump(data, f, default_flow_style=False)
                elif svc.password_config.endswith(".ini"):
                    content = cfg_path.read_text(encoding="utf-8")
                    if "http_username" in content:
                        content = re.sub(r"^http_username\s*=\s*.*$", f"http_username = {username}", content, count=1, flags=re.MULTILINE)
                        content = re.sub(r"^http_password\s*=\s*.*$", f"http_password = {new_password}", content, count=1, flags=re.MULTILINE)
                    else:
                        content = re.sub(r"^username\s*=\s*.*$", f"username = {username}", content, count=1, flags=re.MULTILINE)
                        content = re.sub(r"^password\s*=\s*.*$", f"password = {new_password}", content, count=1, flags=re.MULTILINE)
                    cfg_path.write_text(content, encoding="utf-8")
                updated.append(svc.id)
            except Exception as exc:
                errors.append(f"{svc.id}: {exc}")

        # 5. Update env + secret
        os.environ["STACK_ADMIN_PASSWORD"] = new_password
        self.persist_keys_to_secret({"STACK_ADMIN_PASSWORD": new_password, "STACK_ADMIN_USERNAME": username})

        # 6. Auto-restart file-based services
        restarted = []
        for svc in get_services_with_password_config():
            if svc.id in updated:
                try:
                    self.restart_service(svc.id)
                    restarted.append(svc.id)
                except Exception as exc:
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                    pass

        return {"status": "updated", "services": updated, "errors": errors, "restarted": restarted}

    def _read_key(self, svc: Any, config_root: str) -> str:
        """Read API key for a service using its registry format."""
        reader = _KEY_READERS.get(svc.api_key_format)
        if reader and svc.api_key_config:
            return reader(Path(config_root) / svc.api_key_config)
        return ""

    # -----------------------------------------------------------------------
    # Service restart
    # -----------------------------------------------------------------------

    def restart_service(self, service_name: str) -> dict[str, Any]:
        """Restart a single service container or pod."""
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
                for pod in pods.items:
                    v1.delete_namespaced_pod(name=pod.metadata.name, namespace=namespace)
                return {"status": "restarted", "method": "k8s"}
            else:
                import docker
                client = docker.from_env()
                container = client.containers.get(service_name)
                container.restart(timeout=15)
                return {"status": "restarted", "method": "docker"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)[:80]}

    def batch_restart(self, service_names: list[str]) -> dict[str, Any]:
        """Restart multiple services."""
        from .health import SERVICE_PROBES
        results: dict[str, Any] = {}
        for name in service_names:
            if name in SERVICE_PROBES:
                results[name] = self.restart_service(name)
            else:
                results[name] = {"status": "error", "error": f"unknown service '{name}'"}
        ok = sum(1 for v in results.values() if v.get("status") == "restarted")
        return {"results": results, "restarted": ok, "total": len(service_names)}

    # -----------------------------------------------------------------------
    # K8s secret persistence
    # -----------------------------------------------------------------------

    def persist_keys_to_secret(self, data: dict[str, str]) -> None:
        """Persist key-value pairs to K8s secret if available."""
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if not namespace or not data:
            return
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            v1 = k8s_client.CoreV1Api()
            secret_data = {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
            try:
                existing = v1.read_namespaced_secret("media-stack-secrets", namespace)
                if existing.data:
                    existing.data.update(secret_data)
                else:
                    existing.data = secret_data
                v1.patch_namespaced_secret("media-stack-secrets", namespace, existing)
            except Exception as exc:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                pass
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass


    @staticmethod
    def _load_app_admin_ops(service_id: str) -> Any:
        """Try to import services.apps.<service_id>.admin_ops module.
    
        Returns the module or None if it doesn't exist.
        """
        try:
            import importlib
            return importlib.import_module(f"media_stack.services.apps.{service_id}.admin_ops")
        except (ImportError, ModuleNotFoundError):
            return None


_instance = AdminService()

# Backward compat — callers use module-level functions
is_media_server_reset_path = _instance.is_media_server_reset_path
jellyfin_hard_reset = _instance.jellyfin_hard_reset
hard_reset_service = _instance.hard_reset_service
_discover_jellyfin_admin_user_id = _instance._discover_jellyfin_admin_user_id
rotate_keys = _instance.rotate_keys
reset_password = _instance.reset_password
_read_key = _instance._read_key
restart_service = _instance.restart_service
batch_restart = _instance.batch_restart
persist_keys_to_secret = _instance.persist_keys_to_secret
_load_app_admin_ops = _instance._load_app_admin_ops
