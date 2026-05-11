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
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

import requests
import logging
import yaml

from media_stack.core.config_io import (
    ConfigParseError,
    atomic_write_xml,
    read_and_parse_xml,
    set_or_create_child,
)
from media_stack.core.service_registry.registry import _find_services_dir

_PUT_OK_STATUSES = (HTTPStatus.OK, HTTPStatus.ACCEPTED)
_log = logging.getLogger("media_stack")

# Fully-qualified handler name this module exposes. Used as the
# family-membership signal: a service yaml whose
# ``plugin.compose_preflight_handler`` points here has opted into
# being managed by this preflight, and is therefore a .NET
# Servarr-fork that needs ``<UrlBase>`` set in ``config.xml`` +
# host-config API reconcile. Replaces the hardcoded ``_ARR_APPS``
# list (the smell).
_SELF_PREFLIGHT_HANDLER = (
    "media_stack.services.apps.servarr.http_preflight:run_preflight"
)

_URL_BASE_RECONCILE_TIMEOUT_SEC = 10


@dataclass(frozen=True)
class ServarrFamilyMember:
    """One .NET Servarr fork as declared in a contract YAML.

    Frozen so the registry's cached mapping is safe to share across
    callers without defensive copying. All fields come straight from
    ``contracts/services/<id>.yaml`` — no derivation, no defaults
    that don't also appear in the contract.
    """

    name: str           # service id (= docker container name + log label)
    host: str           # cluster-internal hostname
    port: int           # internal HTTP port
    host_config_url: str  # ``http://<host>:<port><password_api_path>``


class ServarrFamilyRegistry:
    """Registry-driven membership of the .NET Servarr fork family.

    Membership signal: a contract YAML's
    ``plugin.compose_preflight_handler`` references this module's
    ``run_preflight``. This replaces the hardcoded ``_ARR_APPS`` +
    ``_ARR_API_VERSIONS`` dicts that used to live at module scope —
    those required code edits every time a new fork shipped and could
    silently drift from the contract registry. Now adding a sixth
    arr (e.g. ``whisparr``) is a contract-only change: drop a yaml
    in ``contracts/services/``, declare the handler, done.

    The mapping is computed lazily on first access and cached. Call
    :meth:`reload` to invalidate when contracts change at runtime
    (test fixtures, hot-reload).
    """

    def __init__(
        self,
        *,
        services_dir_finder: Any = None,
        self_handler: str = _SELF_PREFLIGHT_HANDLER,
    ) -> None:
        # Constructor-injected finder so tests can swap in a fixture
        # path without monkey-patching the registry module. Defaults
        # to the real registry's locator.
        self._services_dir_finder = services_dir_finder or _find_services_dir
        self._self_handler = self_handler
        self._cached: dict[str, ServarrFamilyMember] | None = None

    def members(self) -> dict[str, ServarrFamilyMember]:
        """Return ``{name: ServarrFamilyMember}`` for every fork."""
        if self._cached is None:
            self._cached = self._load()
        return self._cached

    def names(self) -> tuple[str, ...]:
        """Stable ordering of fork names — matches the YAML glob sort."""
        return tuple(sorted(self.members().keys()))

    def get(self, name: str) -> ServarrFamilyMember | None:
        return self.members().get(name)

    def reload(self) -> None:
        """Drop the cache; next ``members()`` re-reads from disk."""
        self._cached = None

    def _load(self) -> dict[str, ServarrFamilyMember]:
        services_dir = self._services_dir_finder()
        if services_dir is None:
            # No registry on disk (unusual — typically only during
            # certain unit-test contexts that don't ship the contracts).
            # Return empty; callers iterate over zero entries.
            return {}
        members: dict[str, ServarrFamilyMember] = {}
        for yaml_file in sorted(Path(services_dir).glob("*.yaml")):
            if yaml_file.name.startswith("_"):
                continue
            member = self._parse_member(yaml_file)
            if member is not None:
                members[member.name] = member
        return members

    def _parse_member(self, yaml_path: Path) -> ServarrFamilyMember | None:
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            _log.debug(
                "[DEBUG] ServarrFamilyRegistry skipped %s: %s",
                yaml_path.name, exc,
            )
            return None
        plugin = data.get("plugin") or {}
        handler = str(plugin.get("compose_preflight_handler") or "").strip()
        if handler != self._self_handler:
            return None
        svc = data.get("service") or {}
        name = str(svc.get("id") or "").strip()
        if not name:
            return None
        port = int(svc.get("port") or 0)
        if not port:
            return None
        host = str(svc.get("host") or name).strip()
        password_api_path = str(svc.get("password_api_path") or "").strip()
        if not password_api_path:
            # Servarr family always exposes /api/v{N}/config/host;
            # a contract that opted into this preflight but didn't
            # declare the path is a contract bug, not a runtime
            # condition to paper over.
            _log.warning(
                "ServarrFamilyRegistry: %s opted into the servarr "
                "preflight but has no password_api_path — skipping",
                name,
            )
            return None
        return ServarrFamilyMember(
            name=name,
            host=host,
            port=port,
            host_config_url=f"http://{host}:{port}{password_api_path}",
        )


_FAMILY = ServarrFamilyRegistry()


class ServarrHttpPreflight:

    def __init__(
        self,
        env: dict[str, str] | None = None,
        *,
        family: ServarrFamilyRegistry | None = None,
    ) -> None:
        # Sample os.environ at construction so method paths stay
        # off it (class-structure ratchet). Tests pass a fake dict.
        self._env = dict(env) if env is not None else dict(os.environ)
        # Constructor-injected family registry — tests can swap in
        # a fixture-loaded registry without touching the module-level
        # ``_FAMILY`` singleton.
        self._family = family or _FAMILY

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

        for app_name in self._family.names():
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
                member = self._family.get(app_name)
                if member is None:
                    continue
                url = f"http://{member.host}:{member.port}/ping"
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
            # to empty. PUT to ``password_api_path`` (the contract-
            # declared host-config endpoint) persists the value in
            # the DB so it sticks across restarts.
            for app_name in patched:
                self._reconcile_url_base(app_name, log=info)
        else:
            info("ARR preflight: all apps already have correct auth settings")
            # Even when config.xml didn't need patching, the DB-
            # backed UrlBase may have drifted from our desired
            # value — reconcile unconditionally.
            for app_name in self._family.names():
                self._reconcile_url_base(app_name, log=info)

        return {}

    def _reconcile_url_base(
        self, app_name: str, log: Any = None,
    ) -> None:
        """GET the app's host config, PUT if urlBase drifted from
        ``/app/<app_name>``. Silently no-ops on any error — we
        want bootstrap to keep moving even if one app is down."""
        member = self._family.get(app_name)
        if member is None:
            return
        api_key = str(self._env.get(
            f"{app_name.upper()}_API_KEY", "") or "").strip()
        if not api_key:
            if log:
                log(f"ARR preflight: {app_name} no API key in env "
                    "— skipping UrlBase reconcile")
            return
        url = member.host_config_url
        desired = f"/app/{app_name}"
        headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
        cfg = self._fetch_host_config(app_name, url, headers)
        if cfg is None:
            return
        if str(cfg.get("urlBase", "")).strip() == desired:
            return
        cfg["urlBase"] = desired
        self._put_host_config(app_name, url, headers, cfg, desired, log)

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


def _arr_apps_view() -> dict[str, int]:
    """Backward-compat ``{name: port}`` snapshot of the family.

    The legacy module-level ``_ARR_APPS`` dict (now derived from the
    contract registry) — preserved as a function so callers and
    ratchet tests that ``from … import _ARR_APPS`` keep working
    while the source of truth lives in ``ServarrFamilyRegistry``.
    """
    return {m.name: m.port for m in _FAMILY.members().values()}


_ARR_APPS = _arr_apps_view()
