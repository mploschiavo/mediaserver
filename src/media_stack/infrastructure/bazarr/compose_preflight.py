"""Compose preflight for Bazarr's path-prefix base URL.

Envoy serves Bazarr at ``apps.<domain>/app/bazarr/`` and forwards the
prefix on the upstream request. Bazarr's Flask app emits asset URLs
off ``general.base_url`` in ``/config/config/config.yaml``; the
default value is an empty string which works for root-served
deployments but breaks path-prefix routing — the browser receives
absolute ``/UI/<asset>`` references that envoy then 404s, producing
the symptom of a blank page on ``/app/bazarr/``.

Mirrors the shape of :mod:`media_stack.infrastructure.sabnzbd.compose_preflight`
and :mod:`media_stack.infrastructure.qbittorrent.compose_preflight`:
class-based, exec into the running container, idempotent
write-on-drift, restart only when the value actually changed, wait
for readiness before returning. The reconcile is intentionally
scoped to the single ``general.base_url`` line — it does NOT touch
the per-arr ``base_url`` entries under ``sonarr`` / ``radarr`` /
``lidarr`` / ``readarr`` (which are upstream URLs Bazarr uses to
TALK to the arrs, not its own served base).
"""

from __future__ import annotations

import time
from typing import Any, Callable

InfoFn = Callable[[str], None]

_BAZARR_CONFIG_PATH = "/config/config/config.yaml"
_BAZARR_PORT = 6767
_READY_TIMEOUT_SEC = 75
_RESTART_TIMEOUT_SEC = 10


class BazarrComposePreflight:
    """Set Bazarr's served base URL to match the envoy path prefix."""

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _decode_logs(raw: Any) -> str:
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw or "")

    @staticmethod
    def _exec_shell(
        container: Any,
        script: str,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        result = container.exec_run(
            cmd=["sh", "-lc", script],
            environment=dict(env or {}),
            stdout=True,
            stderr=True,
        )
        raw_code = getattr(result, "exit_code", 1)
        code = int(raw_code if raw_code is not None else 1)
        output = _decode_logs(getattr(result, "output", b""))
        return code, output

    @staticmethod
    def _desired_base_url(service_id: str) -> str:
        return f"/app/{service_id}"

    @staticmethod
    def _reconcile_base_url(
        container: Any,
        *,
        desired_base_url: str,
        config_path: str,
    ) -> tuple[bool, str]:
        """Write ``general.base_url: <desired>`` to config.yaml.

        Uses awk to rewrite only the FIRST ``base_url:`` line after the
        ``general:`` header — the per-arr ``base_url`` entries under
        ``sonarr`` / ``radarr`` / etc. (which are upstream URLs Bazarr
        uses to talk TO each arr) come later and must stay ``/``.

        Returns ``(changed, output)``.
        """
        script = """
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
        code, output = _exec_shell(
            container,
            script,
            {
                "BAZARR_CONFIG": config_path,
                "BAZARR_DESIRED_BASE_URL": desired_base_url,
            },
        )
        if code != 0:
            raise RuntimeError(
                "Compose Bazarr preflight failed to reconcile "
                f"{config_path}. Output: {output.strip()}"
            )
        return "__CHANGED__=1" in output, output

    @staticmethod
    def _restart_container(container: Any) -> bool:
        try:
            container.restart(timeout=_RESTART_TIMEOUT_SEC)
        except Exception:
            return False
        return True

    @staticmethod
    def _wait_for_ready(
        container: Any,
        *,
        timeout_seconds: int = _READY_TIMEOUT_SEC,
        port: int = _BAZARR_PORT,
    ) -> bool:
        """Poll the in-container HTTP listener until it answers.

        Bazarr serves the UI from ``general.base_url`` after restart;
        the base URL changes the served path, but the readiness probe
        only needs the listener to bind. Probing ``http://127.0.0.1:<port>/``
        and accepting any 2xx / 3xx / 401 / 403 is sufficient — the
        prefix-stripped root returns 404 once base_url is set, which
        IS proof the listener is up.
        """
        deadline = time.time() + max(1, int(timeout_seconds))
        script = f"""
    tmp_body="/tmp/bazarr-ready.$$"
    code="$(curl -sS -o "$tmp_body" -w "%{{http_code}}" \
      "http://127.0.0.1:{port}/" 2>/dev/null || true)"
    rm -f "$tmp_body" >/dev/null 2>&1 || true
    case "$code" in
      2*|3*|401|403|404) true ;;
      *) false ;;
    esac
    """
        while time.time() < deadline:
            code, _ = _exec_shell(container, script)
            if code == 0:
                return True
            time.sleep(2)
        return False

    def ensure_compose_bazarr_url_base(
        self,
        *,
        compose_env: dict[str, str],
        namespace: str,
        docker: Any,
        info: InfoFn,
        **_: object,
    ) -> dict[str, str]:
        """Compose preflight entry point.

        Signature matches the other ``ensure_compose_*`` handlers so the
        compose-deploy hook resolver dispatches it uniformly.
        """
        # ``namespace`` and ``compose_env`` are part of the contract
        # signature but Bazarr's base URL doesn't depend on either
        # — the envoy path prefix is always ``/app/bazarr`` regardless
        # of namespace. Touching them here just keeps the signature
        # parity for the resolver.
        _ = compose_env
        _ = namespace

        container = docker.get_container("bazarr")
        if container is None:
            info(
                "Compose Bazarr preflight: container 'bazarr' not found; "
                "skipping (not deployed on this profile)."
            )
            return {}

        desired = _desired_base_url("bazarr")
        try:
            changed, _ = _reconcile_base_url(
                container,
                desired_base_url=desired,
                config_path=_BAZARR_CONFIG_PATH,
            )
        except RuntimeError as exc:
            # Common case: config.yaml not yet generated on first boot.
            # Bazarr writes it during first init; the next bootstrap
            # tick will pick it up.
            info(
                f"Compose Bazarr preflight: skipped — {exc}. Will retry "
                "on next bootstrap once Bazarr writes its initial config."
            )
            return {}

        if not changed:
            info(
                "Compose Bazarr preflight: general.base_url already aligned "
                f"({desired})."
            )
            return {}

        if not _restart_container(container):
            raise RuntimeError(
                "Compose Bazarr preflight updated general.base_url but "
                "could not restart container."
            )
        if not _wait_for_ready(container):
            raise RuntimeError(
                "Compose Bazarr preflight restarted container but readiness "
                f"probe failed within {_READY_TIMEOUT_SEC}s."
            )

        info(
            "Compose Bazarr preflight: reconciled general.base_url to "
            f"{desired} and restarted container."
        )
        return {}


_instance = BazarrComposePreflight()
ensure_compose_bazarr_url_base = _instance.ensure_compose_bazarr_url_base


__all__ = [
    "BazarrComposePreflight",
    "ensure_compose_bazarr_url_base",
]
_decode_logs = _instance._decode_logs
_desired_base_url = _instance._desired_base_url
_exec_shell = _instance._exec_shell
_reconcile_base_url = _instance._reconcile_base_url
_restart_container = _instance._restart_container
_text = _instance._text
_wait_for_ready = _instance._wait_for_ready
