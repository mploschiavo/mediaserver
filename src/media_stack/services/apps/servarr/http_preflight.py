"""ARR app preflight: complete setup wizard if needed.

Sonarr/Radarr/Lidarr/Readarr/Prowlarr v4+ start with
AuthenticationRequired=Enabled by default. The API returns HTML
until the setup wizard is completed. This preflight patches the
config.xml to disable auth requirements so the bootstrap pipeline
can use the API.

It also reconciles UrlBase to ``/app/<service>`` via the app's
HTTP API (not just file edits). Prowlarr specifically rehydrates
config.xml from its own SQLite DB on startup and will overwrite
a direct <UrlBase> edit back to empty — so the file patch seeds
the first-boot value and the API call makes it stick.
"""

from __future__ import annotations

import os
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import urlunparse

import requests
import logging

from media_stack.core.config_io import (
    ConfigParseError,
    atomic_write_xml,
    read_and_parse_xml,
    set_or_create_child,
)

_CONFIG_HOST_PATH = "/config/host"
_PUT_OK_STATUSES = (HTTPStatus.OK, HTTPStatus.ACCEPTED)
_log = logging.getLogger("media_stack")


_ARR_APPS = {
    "sonarr": 8989,
    "radarr": 7878,
    "lidarr": 8686,
    "readarr": 8787,
    "prowlarr": 9696,
}

# ARR API versions — Sonarr/Radarr use v3, the rest use v1.
# /config/host is where urlBase lives in every version.
_ARR_API_VERSIONS = {
    "sonarr": "v3",
    "radarr": "v3",
    "lidarr": "v1",
    "readarr": "v1",
    "prowlarr": "v1",
}

_URL_BASE_RECONCILE_TIMEOUT_SEC = 10


class ServarrHttpPreflight:

    def __init__(self, env: dict[str, str] | None = None) -> None:
        # Sample os.environ at construction so method paths stay
        # off it (class-structure ratchet). Tests pass a fake dict.
        self._env = dict(env) if env is not None else dict(os.environ)

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

            try:
                tree = read_and_parse_xml(config_path)
            except ConfigParseError as exc:
                # Refusing to round-trip a corrupt file. The auto-heal
                # job snapshots last-known-good copies and will restore
                # this file on its next sweep; until then bootstrap
                # skips it rather than amplifying the damage.
                info(f"ARR preflight: {app_name} config unparseable ({exc}); "
                     "skipping — auto-heal will restore.")
                continue

            root_el = tree.getroot()
            changed = False
            changed |= set_or_create_child(
                root_el, "AuthenticationMethod", "Forms",
            )
            changed |= set_or_create_child(
                root_el,
                "AuthenticationRequired",
                "DisabledForLocalAddresses",
            )
            changed |= set_or_create_child(
                root_el, "UrlBase", f"/app/{app_name}",
            )

            if not changed:
                continue

            try:
                atomic_write_xml(config_path, tree)
            except ConfigParseError as exc:
                # The atomic writer rolled back from the .bak; we just
                # need to skip this app and keep the bootstrap moving.
                info(f"ARR preflight: {app_name} write+verify failed "
                     f"({exc}); rolled back.")
                continue
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
                        _log.debug("[DEBUG] Swallowed: %s", exc)
                        pass
                    time.sleep(5)

            # API-based UrlBase reconciliation. Prowlarr (and
            # possibly others) rehydrate config.xml from their
            # SQLite DB on startup, overwriting the file edit back
            # to empty. PUT /api/v{N}/config/host persists the
            # value in the DB so it sticks across restarts.
            for app_name in patched:
                self._reconcile_url_base(app_name, log=info)
        else:
            info("ARR preflight: all apps already have correct auth settings")
            # Even when config.xml didn't need patching, the DB-
            # backed UrlBase may have drifted from our desired
            # value — reconcile unconditionally.
            for app_name in _ARR_APPS:
                self._reconcile_url_base(app_name, log=info)

        return {}

    def _reconcile_url_base(
        self, app_name: str, log: Any = None,
    ) -> None:
        """GET the app's host config, PUT if urlBase drifted from
        ``/app/<app_name>``. Silently no-ops on any error — we
        want bootstrap to keep moving even if one app is down."""
        api_key = str(self._env.get(
            f"{app_name.upper()}_API_KEY", "") or "").strip()
        if not api_key:
            if log:
                log(f"ARR preflight: {app_name} no API key in env "
                    "— skipping UrlBase reconcile")
            return
        base = self._host_config_url(app_name)
        desired = f"/app/{app_name}"
        headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
        cfg = self._fetch_host_config(app_name, base, headers)
        if cfg is None:
            return
        if str(cfg.get("urlBase", "")).strip() == desired:
            return
        cfg["urlBase"] = desired
        self._put_host_config(app_name, base, headers, cfg, desired, log)

    def _host_config_url(self, app_name: str) -> str:
        port = _ARR_APPS[app_name]
        api_ver = _ARR_API_VERSIONS.get(app_name, "v1")
        return urlunparse((
            "http", f"{app_name}:{port}",
            f"/api/{api_ver}{_CONFIG_HOST_PATH}", "", "", "",
        ))

    def _fetch_host_config(
        self, app_name: str, url: str, headers: dict,
    ) -> dict | None:
        try:
            resp = requests.get(
                url, headers=headers,
                timeout=_URL_BASE_RECONCILE_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] UrlBase GET %s failed: %s", app_name, exc,
            )
            return None
        if resp.status_code != HTTPStatus.OK:
            return None
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return None

    def _put_host_config(
        self, app_name: str, url: str, headers: dict, cfg: dict,
        desired: str, log: Any,
    ) -> None:
        try:
            put = requests.put(
                url, headers=headers, json=cfg,
                timeout=_URL_BASE_RECONCILE_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] UrlBase PUT %s failed: %s", app_name, exc,
            )
            return
        if put.status_code in _PUT_OK_STATUSES:
            if log:
                log(f"ARR preflight: {app_name} UrlBase -> "
                    f"{desired} (persisted via API)")
            return
        _log.debug(
            "[DEBUG] UrlBase PUT %s returned %s",
            app_name, put.status_code,
        )

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
            _log.debug("[DEBUG] Swallowed: %s", exc)
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
