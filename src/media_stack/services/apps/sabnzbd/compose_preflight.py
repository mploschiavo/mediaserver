"""Compose preflight hooks for SABnzbd API accessibility."""

from __future__ import annotations

import re
import time
from typing import Any, Callable

InfoFn = Callable[[str], None]

_MARKER_RE = re.compile(r"^__(?P<key>[A-Z_]+)__=(?P<value>.*)$", re.MULTILINE)


class SabnzbdComposePreflight:

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _decode_logs(raw: Any) -> str:
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw or "")

    @staticmethod
    def _split_csv(token: str) -> list[str]:
        return [part.strip() for part in str(token or "").split(",") if part.strip()]

    @staticmethod
    def _dedupe_csv(items: list[str]) -> str:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            token = _text(item)
            if not token:
                continue
            if token in seen:
                continue
            seen.add(token)
            out.append(token)
        return ",".join(out)

    @staticmethod
    def _exec_shell(container: Any, script: str, env: dict[str, str] | None = None) -> tuple[int, str]:
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
    def _parse_markers(output: str) -> dict[str, str]:
        markers: dict[str, str] = {}
        for match in _MARKER_RE.finditer(str(output or "")):
            markers[match.group("key")] = match.group("value")
        return markers

    @staticmethod
    def _desired_host_whitelist(compose_env: dict[str, str], namespace: str) -> str:
        service_host = _text(compose_env.get("SAB_SERVICE_HOST")) or "sabnzbd"
        ingress_host = (
            _text(compose_env.get("SABNZBD_HOST"))
            or _text(compose_env.get("SAB_INGRESS_HOST"))
            or "sabnzbd.local"
        )
        gateway_host = _text(compose_env.get("APP_GATEWAY_HOST"))
        append_hosts = _split_csv(_text(compose_env.get("SAB_HOST_WHITELIST_APPEND")))
        hosts: list[str] = [
            service_host,
            f"{service_host}.{namespace}",
            f"{service_host}.{namespace}.svc",
            f"{service_host}.{namespace}.svc.cluster.local",
            ingress_host,
            gateway_host,
            "localhost",
            "127.0.0.1",
        ]
        hosts.extend(append_hosts)
        return _dedupe_csv(hosts)

    @staticmethod
    def _desired_local_ranges(compose_env: dict[str, str]) -> str:
        seed = _text(compose_env.get("SAB_LOCAL_RANGES"))
        if not seed:
            seed = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
        return _dedupe_csv(_split_csv(seed))

    @staticmethod
    def _reconcile_sabnzbd_config(
        container: Any,
        *,
        host_whitelist: str,
        local_ranges: str,
        download_dir: str,
        complete_dir: str,
        auto_browser: str,
    ) -> tuple[bool, str]:
        script = """
    set -eu
    conf="/config/sabnzbd.ini"
    [ -f "$conf" ] || { echo "__ERR__=missing_config"; exit 21; }

    current_hw="$(awk -F "=" '/^host_whitelist[[:space:]]*=/{print $2; exit}' "$conf" | tr -d " " || true)"
    current_lr="$(awk -F "=" '/^local_ranges[[:space:]]*=/{print $2; exit}' "$conf" | tr -d " " || true)"

    dedupe_csv() {
      printf "%s" "$1" \
        | tr "," "\n" \
        | sed "s/^[[:space:]]*//;s/[[:space:]]*$//" \
        | awk "NF && !seen[\\$0]++" \
        | paste -sd "," -
    }

    desired_hw="$(dedupe_csv "${current_hw},${SAB_HOST_WHITELIST}")"
    desired_lr="$(dedupe_csv "${current_lr},${SAB_LOCAL_RANGES}")"
    desired_dl="${SAB_DOWNLOAD_DIR}"
    desired_cp="${SAB_COMPLETE_DIR}"
    desired_ab="${SAB_AUTO_BROWSER}"

    before="$(grep -E "^(host_whitelist|local_ranges|download_dir|complete_dir|auto_browser)[[:space:]]*=" "$conf" 2>/dev/null || true)"

    if grep -q "^host_whitelist[[:space:]]*=" "$conf"; then
      sed -i "s#^host_whitelist[[:space:]]*=.*#host_whitelist = ${desired_hw}#" "$conf"
    else
      echo "host_whitelist = ${desired_hw}" >>"$conf"
    fi

    if grep -q "^local_ranges[[:space:]]*=" "$conf"; then
      sed -i "s#^local_ranges[[:space:]]*=.*#local_ranges = ${desired_lr}#" "$conf"
    else
      echo "local_ranges = ${desired_lr}" >>"$conf"
    fi

    if grep -q "^download_dir[[:space:]]*=" "$conf"; then
      sed -i "s#^download_dir[[:space:]]*=.*#download_dir = ${desired_dl}#" "$conf"
    else
      echo "download_dir = ${desired_dl}" >>"$conf"
    fi

    if grep -q "^complete_dir[[:space:]]*=" "$conf"; then
      sed -i "s#^complete_dir[[:space:]]*=.*#complete_dir = ${desired_cp}#" "$conf"
    else
      echo "complete_dir = ${desired_cp}" >>"$conf"
    fi

    if grep -q "^auto_browser[[:space:]]*=" "$conf"; then
      sed -i "s#^auto_browser[[:space:]]*=.*#auto_browser = ${desired_ab}#" "$conf"
    else
      echo "auto_browser = ${desired_ab}" >>"$conf"
    fi

    after="$(grep -E "^(host_whitelist|local_ranges|download_dir|complete_dir|auto_browser)[[:space:]]*=" "$conf" 2>/dev/null || true)"
    changed=0
    [ "$before" = "$after" ] || changed=1

    echo "__CHANGED__=${changed}"
    echo "__HOST_WHITELIST__=${desired_hw}"
    echo "__LOCAL_RANGES__=${desired_lr}"
    """
        code, output = _exec_shell(
            container,
            script,
            {
                "SAB_HOST_WHITELIST": host_whitelist,
                "SAB_LOCAL_RANGES": local_ranges,
                "SAB_DOWNLOAD_DIR": download_dir,
                "SAB_COMPLETE_DIR": complete_dir,
                "SAB_AUTO_BROWSER": auto_browser,
            },
        )
        if code != 0:
            raise RuntimeError(
                "Compose SABnzbd preflight failed to reconcile /config/sabnzbd.ini. "
                f"Output: {output.strip()}"
            )
        markers = _parse_markers(output)
        return markers.get("CHANGED", "") == "1", output

    @staticmethod
    def _restart_container(container: Any) -> bool:
        try:
            container.restart(timeout=10)
        except Exception:
            return False
        return True

    @staticmethod
    def _wait_for_ready(container: Any, *, timeout_seconds: int = 60) -> bool:
        deadline = time.time() + max(1, int(timeout_seconds))
        script = """
    tmp_body="/tmp/sab-ready-body.$$"
    code="$(curl -sS -o "$tmp_body" -w "%{http_code}" \
      "http://127.0.0.1:8080/" 2>/dev/null || true)"
    rm -f "$tmp_body" >/dev/null 2>&1 || true
    case "$code" in
      2*|3*|401|403) true ;;
      *) false ;;
    esac
    """
        while time.time() < deadline:
            code, _ = _exec_shell(container, script)
            if code == 0:
                return True
            time.sleep(2)
        return False

    def ensure_compose_sabnzbd_api_access(self, 
        *,
        compose_env: dict[str, str],
        namespace: str,
        docker: Any,
        info: InfoFn,
        **_: object,
    ) -> dict[str, str]:
        container = docker.get_container("sabnzbd")
        if container is None:
            info("Compose SABnzbd preflight: container 'sabnzbd' not found; skipping.")
            return {}

        resolved_namespace = _text(namespace) or "media-stack"
        host_whitelist = _desired_host_whitelist(compose_env, resolved_namespace)
        local_ranges = _desired_local_ranges(compose_env)
        changed, output = _reconcile_sabnzbd_config(
            container,
            host_whitelist=host_whitelist,
            local_ranges=local_ranges,
            download_dir="/data/usenet/incomplete",
            complete_dir="/data/usenet/completed",
            auto_browser="0",
        )
        if not changed:
            info(
                "Compose SABnzbd preflight: api-access settings already aligned "
                "(host_whitelist/local_ranges)."
            )
            return {}

        if not _restart_container(container):
            raise RuntimeError(
                "Compose SABnzbd preflight updated config but could not restart container."
            )
        if not _wait_for_ready(container, timeout_seconds=75):
            raise RuntimeError(
                "Compose SABnzbd preflight restarted container but readiness probe failed."
            )

        markers = _parse_markers(output)
        info(
            "Compose SABnzbd preflight: reconciled api-access settings and restarted "
            f"container (host_whitelist={_text(markers.get('HOST_WHITELIST'))}, "
            f"local_ranges={_text(markers.get('LOCAL_RANGES'))})."
        )
        return {}


_instance = SabnzbdComposePreflight()
ensure_compose_sabnzbd_api_access = _instance.ensure_compose_sabnzbd_api_access


__all__ = ["ensure_compose_sabnzbd_api_access"]
_decode_logs = _instance._decode_logs
_dedupe_csv = _instance._dedupe_csv
_desired_host_whitelist = _instance._desired_host_whitelist
_desired_local_ranges = _instance._desired_local_ranges
_exec_shell = _instance._exec_shell
_parse_markers = _instance._parse_markers
_reconcile_sabnzbd_config = _instance._reconcile_sabnzbd_config
_restart_container = _instance._restart_container
_split_csv = _instance._split_csv
_text = _instance._text
_wait_for_ready = _instance._wait_for_ready
