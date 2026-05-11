"""Compose preflight for Bazarr's path-prefix base URL.

Envoy serves Bazarr at ``apps.<domain>/app/bazarr/`` and forwards the
prefix on the upstream request. Bazarr's Flask app emits asset URLs
off ``general.base_url`` in ``/config/config/config.yaml``; the
default value is an empty string which works for root-served
deployments but breaks path-prefix routing — the browser receives
absolute ``/UI/<asset>`` references that envoy then 404s, producing
the symptom of a blank page on ``/app/bazarr/``.

Design — Strategy + Adapter:

* :class:`BazarrBaseUrlReconciler` is the Strategy that knows the
  Bazarr-specific YAML edit (awk-rewrite the first ``base_url:`` line
  inside the ``general:`` block). It reads/writes through the
  injected :class:`ContainerAccess` Adapter (compose-side
  :class:`ComposeContainerAccess`), which is the same Protocol the
  lifecycle layer uses for credentials rotation — no duplicate
  exec/restart machinery, no parallel static helper namespace.
* :class:`BazarrReadinessProbe` polls the in-container HTTP listener
  through the same ``ContainerAccess`` port; constructor-injected
  ``time_provider`` + ``sleep_fn`` so unit tests don't have to
  monkeypatch :mod:`time`.
* :class:`BazarrComposePreflight` is the entry-point class: bound to
  the contract YAML via the module-level
  ``ensure_compose_bazarr_url_base`` shim, it takes the
  resolver-supplied ``docker`` adapter, builds a
  ``ComposeContainerAccess`` for the bazarr container, hands it to
  the reconciler + probe, restarts only on drift.

The reconcile is intentionally scoped to the single
``general.base_url`` line — it does NOT touch the per-arr ``base_url``
entries under ``sonarr`` / ``radarr`` / ``lidarr`` / ``readarr``
(which are upstream URLs Bazarr uses to TALK to the arrs, not its
own served base).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from media_stack.domain.services.container_access import (
    ContainerAccess,
    ContainerAccessError,
)
from media_stack.infrastructure.platforms.compose.container_access import (
    ComposeContainerAccess,
)

InfoFn = Callable[[str], None]
TimeProvider = Callable[[], float]
SleepFn = Callable[[float], None]

_BAZARR_CONFIG_PATH = "/config/config/config.yaml"
_BAZARR_PORT = 6767
_READY_TIMEOUT_SEC = 75
_RESTART_TIMEOUT_SEC = 10
_READY_POLL_INTERVAL_SEC = 2

_RECONCILE_SCRIPT = """
set -eu
conf="${BAZARR_CONFIG}"
desired="${BAZARR_DESIRED_BASE_URL}"
[ -f "$conf" ] || { echo "__ERR__=missing_config"; exit 21; }

# awk: in general: block only, rewrite the first base_url line.
# Quotes (single) match how Bazarr serializes empty values:
# ``base_url: ''``. We emit the same quoting style for non-empty
# values so the YAML stays canonical.
tmp="${conf}.preflight.$$"
awk -v desired="$desired" '
  BEGIN { in_general=0; replaced=0 }
  /^general:/ { in_general=1; print; next }
  in_general && /^[a-z][a-z_]*:/ && !/^general:/ {
    in_general=0
  }
  in_general && !replaced && /^[[:space:]]+base_url:/ {
    printf "  base_url: %c%s%c\\n", 39, desired, 39
    replaced=1
    next
  }
  { print }
' "$conf" > "$tmp"

if cmp -s "$conf" "$tmp"; then
  rm -f "$tmp"
  echo "__CHANGED__=0"
  exit 0
fi

mv "$tmp" "$conf"
echo "__CHANGED__=1"
"""


class BazarrBaseUrlReconciler:
    """Strategy: write ``general.base_url`` if drifted.

    Talks to the running Bazarr container through an injected
    :class:`ContainerAccess`, so the same code path works for any
    platform that ships a ``ContainerAccess`` impl (today: compose;
    later: k8s when bazarr's served-base setting is needed there too).
    """

    def __init__(
        self,
        *,
        container_access: ContainerAccess,
        config_path: str = _BAZARR_CONFIG_PATH,
    ) -> None:
        self._container_access = container_access
        self._config_path = config_path

    def desired_for(self, service_id: str) -> str:
        """Path-prefix Bazarr should serve from when fronted by Envoy.

        Lifted to instance method so subclasses can override the
        ``/app/<id>`` convention (e.g. a deployment that mounts Bazarr
        at a non-default prefix). The reconciler stays unaware of
        the convention's source — it just writes the value it's told.
        """
        return f"/app/{service_id}"

    def apply(self, desired_base_url: str) -> bool:
        """Return ``True`` when the on-disk value actually changed."""
        env = {
            "BAZARR_CONFIG": self._config_path,
            "BAZARR_DESIRED_BASE_URL": desired_base_url,
        }
        try:
            code, output = self._container_access.exec_shell(
                _RECONCILE_SCRIPT, env=env,
            )
        except ContainerAccessError as exc:
            raise RuntimeError(
                f"Compose Bazarr preflight failed to reconcile "
                f"{self._config_path}: {exc}",
            ) from exc
        if code != 0:
            raise RuntimeError(
                f"Compose Bazarr preflight failed to reconcile "
                f"{self._config_path}. Output: {output.strip()}",
            )
        return "__CHANGED__=1" in output


class BazarrReadinessProbe:
    """Poll the in-container HTTP listener until it answers.

    Bazarr serves the UI from ``general.base_url`` after restart;
    the base URL changes the served path, but the readiness probe
    only needs the listener to bind. Probing ``/`` and accepting any
    2xx / 3xx / 401 / 403 / 404 is sufficient — the prefix-stripped
    root returns 404 once base_url is set, which IS proof the
    listener is up.
    """

    def __init__(
        self,
        *,
        container_access: ContainerAccess,
        port: int = _BAZARR_PORT,
        timeout_seconds: int = _READY_TIMEOUT_SEC,
        poll_interval_seconds: int = _READY_POLL_INTERVAL_SEC,
        time_provider: TimeProvider = time.time,
        sleep_fn: SleepFn = time.sleep,
    ) -> None:
        self._container_access = container_access
        self._port = port
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._time = time_provider
        self._sleep = sleep_fn

    def wait_until_ready(self) -> bool:
        script = self._build_probe_script()
        deadline = self._time() + max(1, int(self._timeout_seconds))
        while self._time() < deadline:
            try:
                code, _ = self._container_access.exec_shell(script)
            except ContainerAccessError:
                code = 1
            if code == 0:
                return True
            self._sleep(self._poll_interval_seconds)
        return False

    def _build_probe_script(self) -> str:
        return (
            'tmp_body="/tmp/bazarr-ready.$$"\n'
            f'code="$(curl -sS -o "$tmp_body" -w "%{{http_code}}" '
            f'"http://127.0.0.1:{self._port}/" 2>/dev/null || true)"\n'
            'rm -f "$tmp_body" >/dev/null 2>&1 || true\n'
            'case "$code" in 2*|3*|401|403|404) true ;; *) false ;; esac\n'
        )


class BazarrComposePreflight:
    """Entry-point class for the contract's ``compose_preflight_handler``.

    Wired in ``contracts/services/bazarr.yaml::plugin.compose_preflight_handler``
    via the module-level shim ``ensure_compose_bazarr_url_base`` below.
    The resolver passes a ``docker`` adapter; this class adapts it
    into the platform-shared :class:`ComposeContainerAccess` and
    drives the reconcile-then-restart flow through composed
    strategies (no static helpers).
    """

    def __init__(
        self,
        *,
        service_id: str = "bazarr",
        config_path: str = _BAZARR_CONFIG_PATH,
        port: int = _BAZARR_PORT,
        ready_timeout_seconds: int = _READY_TIMEOUT_SEC,
        restart_timeout_seconds: int = _RESTART_TIMEOUT_SEC,
        time_provider: TimeProvider = time.time,
        sleep_fn: SleepFn = time.sleep,
    ) -> None:
        self._service_id = service_id
        self._config_path = config_path
        self._port = port
        self._ready_timeout_seconds = ready_timeout_seconds
        self._restart_timeout_seconds = restart_timeout_seconds
        self._time = time_provider
        self._sleep = sleep_fn

    def ensure_compose_bazarr_url_base(
        self,
        *,
        docker: Any,
        info: InfoFn,
        **_: object,
    ) -> dict[str, str]:
        """Compose preflight entry point.

        Signature matches the other ``ensure_compose_*`` handlers so
        the compose-deploy hook resolver dispatches it uniformly.
        ``compose_env``/``namespace``/etc. arrive via ``**_`` — Bazarr's
        base URL doesn't depend on them (envoy path prefix is always
        ``/app/bazarr`` regardless of namespace), so we don't bind
        them.
        """
        container = docker.get_container(self._service_id)
        if container is None:
            info(
                f"Compose Bazarr preflight: container '{self._service_id}' "
                "not found; skipping (not deployed on this profile).",
            )
            return {}

        container_access = ComposeContainerAccess(container)
        reconciler = BazarrBaseUrlReconciler(
            container_access=container_access,
            config_path=self._config_path,
        )
        desired = reconciler.desired_for(self._service_id)

        try:
            changed = reconciler.apply(desired)
        except RuntimeError as exc:
            # Common case: config.yaml not yet generated on first
            # boot. Bazarr writes it during first init; the next
            # bootstrap tick picks it up.
            info(
                f"Compose Bazarr preflight: skipped — {exc}. Will retry "
                "on next bootstrap once Bazarr writes its initial config.",
            )
            return {}

        if not changed:
            info(
                "Compose Bazarr preflight: general.base_url already aligned "
                f"({desired}).",
            )
            return {}

        if not container_access.restart(
            timeout_seconds=self._restart_timeout_seconds,
        ):
            raise RuntimeError(
                "Compose Bazarr preflight updated general.base_url but "
                "could not restart container.",
            )
        probe = BazarrReadinessProbe(
            container_access=container_access,
            port=self._port,
            timeout_seconds=self._ready_timeout_seconds,
            time_provider=self._time,
            sleep_fn=self._sleep,
        )
        if not probe.wait_until_ready():
            raise RuntimeError(
                "Compose Bazarr preflight restarted container but readiness "
                f"probe failed within {self._ready_timeout_seconds}s.",
            )

        info(
            "Compose Bazarr preflight: reconciled general.base_url to "
            f"{desired} and restarted container.",
        )
        return {}


# Module-level singleton + handler shim — the contract YAML's
# ``plugin.compose_preflight_handler`` reference resolves to this
# callable. Constructing the singleton at import keeps the resolver
# fast (no class instantiation per dispatch) while leaving the class
# itself testable in isolation.
_instance = BazarrComposePreflight()
ensure_compose_bazarr_url_base = _instance.ensure_compose_bazarr_url_base

__all__ = [
    "BazarrBaseUrlReconciler",
    "BazarrComposePreflight",
    "BazarrReadinessProbe",
    "ensure_compose_bazarr_url_base",
]
