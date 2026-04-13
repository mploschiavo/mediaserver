"""Post-bootstrap: restart apps that need to pick up config changes.

After bootstrap sets urlBase, auth settings, and other config values,
the apps need a restart to reload config.xml. This handler restarts
them via Docker SDK (compose) or K8s pod delete (kubernetes).
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests



# _APPS_TO_RESTART initialized after class




class RestartAppsService:
    """Wraps app restart preflight logic."""

    def write_config_and_restart(
        self,
        *,
        config_root: str = "/srv-config",
        log: Any = None,
        **kwargs: Any,
    ) -> dict[str, str]:
        """Restart apps that need to pick up bootstrap config changes."""

        def info(msg: str) -> None:
            if log:
                log(msg)

        restarted: list[str] = []
        for app_name, port in _APPS_TO_RESTART:
            try:
                _restart(app_name, info)
                restarted.append(app_name)
            except Exception as exc:
                info(f"App restart: {app_name} skipped ({exc})")

        if restarted:
            info(f"App restart: restarted {len(restarted)} apps, waiting for readiness...")
            time.sleep(10)
            for app_name, port in _APPS_TO_RESTART:
                if app_name not in restarted:
                    continue
                url = f"http://{app_name}:{port}/ping"
                deadline = time.time() + 90
                while time.time() < deadline:
                    try:
                        resp = requests.get(url, timeout=5)
                        if resp.status_code in (200, 401, 403):
                            info(f"App restart: {app_name} ready")
                            break
                    except Exception as exc:
                        import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                        pass
                    time.sleep(5)

        return {}


    @staticmethod
    def _apps_to_restart() -> list[tuple[str, int]]:
        """Build restart list from the service registry — no hardcoded service names."""
        try:
            from media_stack.api.services.registry import SERVICES
            return [(s.id, s.port) for s in SERVICES if s.port > 0 and s.health_path]
        except Exception:
            return []

    @staticmethod
    def _restart(app_name: str, info: Any) -> None:
        """Restart via Docker SDK or K8s pod delete."""
        try:
            import docker
    
            client = docker.from_env()
            container = client.containers.get(app_name)
            container.restart(timeout=15)
            info(f"App restart: {app_name} (Docker)")
            return
        except Exception as exc:
            import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        try:
            from kubernetes import client, config
    
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            v1 = client.CoreV1Api()
            namespace = os.environ.get("K8S_NAMESPACE", "media-stack")
            pods = v1.list_namespaced_pod(
                namespace=namespace, label_selector=f"app={app_name}"
            )
            for pod in pods.items:
                v1.delete_namespaced_pod(name=pod.metadata.name, namespace=namespace)
            info(f"App restart: {app_name} (K8s)")
            return
        except Exception as exc:
            raise RuntimeError(f"Cannot restart {app_name}: {exc}") from exc


_instance = RestartAppsService()
write_config_and_restart = _instance.write_config_and_restart
_apps_to_restart = _instance._apps_to_restart
_APPS_TO_RESTART = _apps_to_restart()
_restart = _instance._restart
