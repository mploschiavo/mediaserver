"""ARR app preflight: complete setup wizard if needed.

Sonarr/Radarr/Lidarr/Readarr/Prowlarr v4+ start with
AuthenticationRequired=Enabled by default. The API returns HTML
until the setup wizard is completed. This preflight patches the
config.xml to disable auth requirements so the bootstrap pipeline
can use the API.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import requests
import logging


_ARR_APPS = {
    "sonarr": 8989,
    "radarr": 7878,
    "lidarr": 8686,
    "readarr": 8787,
    "prowlarr": 9696,
}


class ServarrHttpPreflight:

    def run_preflight(self, 
        *,
        config_root: str = "/srv-config",
        log: Any = None,
        **kwargs: Any,
    ) -> dict[str, str]:
        """Patch ARR app config.xml files to disable auth for bootstrap.

        Sets AuthenticationRequired=DisabledForLocalAddresses so the API
        is accessible from the bootstrap runner without completing the
        setup wizard.
        """

        def info(msg: str) -> None:
            if log:
                log(msg)

        root = Path(config_root)
        patched: list[str] = []

        for app_name in _ARR_APPS:
            config_path = root / app_name / "config.xml"
            if not config_path.exists():
                continue

            text = config_path.read_text(encoding="utf-8", errors="replace")
            original = text

            # Dismiss the setup wizard and set urlBase for path-prefix routing.
            # AuthenticationMethod=Forms + DisabledForLocalAddresses allows
            # API access from pod IPs without credentials.
            text = re.sub(
                r"<AuthenticationMethod>[^<]*</AuthenticationMethod>",
                "<AuthenticationMethod>Forms</AuthenticationMethod>",
                text,
            )
            text = re.sub(
                r"<AuthenticationRequired>[^<]*</AuthenticationRequired>",
                "<AuthenticationRequired>DisabledForLocalAddresses</AuthenticationRequired>",
                text,
            )
            # Set UrlBase for path-prefix routing (/app/<service>).
            desired_url_base = f"/app/{app_name}"
            current_url_base = re.search(r"<UrlBase>([^<]*)</UrlBase>", text)
            if current_url_base and current_url_base.group(1) != desired_url_base:
                text = re.sub(
                    r"<UrlBase>[^<]*</UrlBase>",
                    f"<UrlBase>{desired_url_base}</UrlBase>",
                    text,
                )

            if text != original:
                config_path.write_text(text, encoding="utf-8")
                patched.append(app_name)
                info(f"ARR preflight: patched {app_name} AuthenticationRequired=DisabledForLocalAddresses")

        if patched:
            import time

            info(f"ARR preflight: patched {len(patched)} apps, restarting...")
            for app_name in patched:
                _restart_app(app_name, log=info)

            # In K8s, pod deletion + recreation takes 30-60s. Wait for DNS
            # to go away (old pod terminating) then wait for new pod to respond.
            info("ARR preflight: waiting 15s for pod recreation...")
            time.sleep(15)

            # Wait for all patched apps to respond to /ping.
            for app_name in patched:
                port = _ARR_APPS[app_name]
                url = f"http://{app_name}:{port}/ping"
                deadline = time.time() + 90
                while time.time() < deadline:
                    try:
                        resp = requests.get(url, timeout=5)
                        if resp.status_code == 200:
                            info(f"ARR preflight: {app_name} ready")
                            break
                    except Exception as exc:
                        logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                        pass
                    time.sleep(5)
        else:
            info("ARR preflight: all apps already have correct auth settings")

        return {}

    @staticmethod
    def _restart_app(app_name: str, log: Any = None) -> None:
        """Restart an app — Docker SDK or K8s pod delete."""
        try:
            import docker

            client = docker.from_env()
            container = client.containers.get(app_name)
            container.restart(timeout=15)
            if log:
                log(f"ARR preflight: restarted {app_name} (Docker)")
            return
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        try:
            import os

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
            if log:
                log(f"ARR preflight: restarted {app_name} (K8s pod delete)")
        except Exception:
            if log:
                log(f"ARR preflight: could not restart {app_name}")


_instance = ServarrHttpPreflight()
run_preflight = _instance.run_preflight
_restart_app = _instance._restart_app
