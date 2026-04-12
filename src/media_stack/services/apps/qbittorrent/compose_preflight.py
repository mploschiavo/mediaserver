"""Compose preflight hooks for qBittorrent credential reconciliation."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

_TEMP_PASSWORD_RE = re.compile(r"temporary password[^:]*:\s*(\S+)", re.IGNORECASE)

InfoFn = Callable[[str], None]


class QbittorrentComposePreflight:

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _decode_logs(raw: Any) -> str:
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw or "")

    @staticmethod
    def _extract_temporary_password(log_text: str) -> str:
        matches = list(_TEMP_PASSWORD_RE.finditer(str(log_text or "")))
        if not matches:
            return ""
        return _text(matches[-1].group(1))

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
    def _login_with_container(container: Any, username: str, password: str) -> bool:
        script = """
    tmp_body="/tmp/qb-login-body.$$"
    code="$(curl -sS -o "$tmp_body" -w "%{http_code}" \
      -H "Origin: http://127.0.0.1:8080" \
      -H "Referer: http://127.0.0.1:8080/" \
      -H "User-Agent: media-stack-controller/1.0" \
      --data-urlencode "username=$QB_USER" \
      --data-urlencode "password=$QB_PASS" \
      "http://127.0.0.1:8080/api/v2/auth/login" 2>/dev/null || true)"
    body="$(cat "$tmp_body" 2>/dev/null || true)"
    rm -f "$tmp_body" >/dev/null 2>&1 || true
    case "$code" in
      2*) [ "$body" = "Ok." ] || [ "${body#Ok.}" != "$body" ] ;;
      *) false ;;
    esac
    """
        code, _ = _exec_shell(
            container,
            script,
            {
                "QB_USER": username,
                "QB_PASS": password,
            },
        )
        return code == 0

    @staticmethod
    def _set_credentials_with_container(
        container: Any,
        *,
        auth_user: str,
        auth_pass: str,
        target_user: str,
        target_pass: str,
    ) -> bool:
        prefs_json = json.dumps(
            {
                "web_ui_username": target_user,
                "web_ui_password": target_pass,
            },
            separators=(",", ":"),
        )
        script = """
    tmp_login="/tmp/qb-login-body.$$"
    tmp_pref="/tmp/qb-pref-body.$$"
    cookie="/tmp/qb-cookie.$$"
    code_login="$(curl -sS -o "$tmp_login" -w "%{http_code}" \
      -c "$cookie" \
      -H "Origin: http://127.0.0.1:8080" \
      -H "Referer: http://127.0.0.1:8080/" \
      -H "User-Agent: media-stack-controller/1.0" \
      --data-urlencode "username=$AUTH_USER" \
      --data-urlencode "password=$AUTH_PASS" \
      "http://127.0.0.1:8080/api/v2/auth/login" 2>/dev/null || true)"
    body_login="$(cat "$tmp_login" 2>/dev/null || true)"
    case "$code_login" in
      2*) [ "$body_login" = "Ok." ] || [ "${body_login#Ok.}" != "$body_login" ] || exit 41 ;;
      *) exit 42 ;;
    esac
    code_pref="$(curl -sS -o "$tmp_pref" -w "%{http_code}" \
      -b "$cookie" \
      -H "Origin: http://127.0.0.1:8080" \
      -H "Referer: http://127.0.0.1:8080/" \
      -H "User-Agent: media-stack-controller/1.0" \
      --data-urlencode "json=$PREFERENCES_JSON" \
      "http://127.0.0.1:8080/api/v2/app/setPreferences" 2>/dev/null || true)"
    rm -f "$tmp_login" "$tmp_pref" "$cookie" >/dev/null 2>&1 || true
    case "$code_pref" in
      2*) true ;;
      *) false ;;
    esac
    """
        code, _ = _exec_shell(
            container,
            script,
            {
                "AUTH_USER": auth_user,
                "AUTH_PASS": auth_pass,
                "PREFERENCES_JSON": prefs_json,
            },
        )
        return code == 0

    @staticmethod
    def _reset_auth_config_in_container(container: Any) -> bool:
        script = """
    set -e
    found=0
    for f in $(find /config -maxdepth 6 -name qBittorrent.conf 2>/dev/null); do
      found=1
      cp "$f" "${f}.bak.$(date +%s)" || true
      sed -i \
        -e '/^WebUI\\\\Username=/d' \
        -e '/^WebUI\\\\Password_PBKDF2=/d' \
        -e '/^WebUI\\\\Password_ha1=/d' \
        -e '/^WebUI\\\\LocalHostAuth=/d' \
        -e '/^WebUI\\\\MaxAuthenticationFailCount=/d' \
        -e '/^WebUI\\\\BanDuration=/d' \
        "$f"
    done
    [ "$found" -eq 1 ] || exit 21
    """
        code, _ = _exec_shell(container, script)
        return code == 0

    @staticmethod
    def _restart_container(container: Any) -> bool:
        try:
            container.restart(timeout=10)
        except Exception:
            return False
        return True

    @staticmethod
    def _wait_for_webui_ready(container: Any, *, timeout_seconds: int = 60) -> bool:
        deadline = time.time() + max(1, int(timeout_seconds))
        probe = """
    tmp_body="/tmp/qb-ready-body.$$"
    code="$(curl -sS -o "$tmp_body" -w "%{http_code}" \
      "http://127.0.0.1:8080/api/v2/app/version" 2>/dev/null || true)"
    rm -f "$tmp_body" >/dev/null 2>&1 || true
    case "$code" in
      2*|401|403) true ;;
      *) false ;;
    esac
    """
        while time.time() < deadline:
            code, _ = _exec_shell(container, probe)
            if code == 0:
                return True
            time.sleep(2)
        return False

    @staticmethod
    def _wait_for_login(
        container: Any, username: str, password: str, *, timeout_seconds: int = 90
    ) -> bool:
        timeout_value = max(1, int(timeout_seconds))
        if not _wait_for_webui_ready(container, timeout_seconds=min(timeout_value, 45)):
            return False
        attempts = 2 if timeout_value >= 20 else 1
        for _ in range(attempts):
            if _login_with_container(container, username, password):
                return True
            time.sleep(2)
        return False

    @staticmethod
    def _read_temporary_password(container: Any, *, timeout_seconds: int = 45) -> str:
        deadline = time.time() + max(1, int(timeout_seconds))
        while time.time() < deadline:
            logs = _decode_logs(container.logs(stdout=True, stderr=True, tail=600))
            token = _extract_temporary_password(logs)
            if token:
                return token
            time.sleep(2)
        return ""

    @staticmethod
    def _upsert_env_file(path: Path, updates: dict[str, str]) -> None:
        if not updates:
            return
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        updated_lines = list(lines)
        key_to_index: dict[str, int] = {}
        for idx, raw_line in enumerate(updated_lines):
            line = str(raw_line or "").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, _ = line.partition("=")
            token = _text(key)
            if token:
                key_to_index[token] = idx
        for key, value in updates.items():
            if key in key_to_index:
                updated_lines[key_to_index[key]] = f"{key}={value}"
                continue
            updated_lines.append(f"{key}={value}")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(updated_lines).rstrip() + "\n"
        path.write_text(payload, encoding="utf-8")

    def ensure_compose_torrent_client_credentials(self, 
        *,
        compose_env: dict[str, str],
        compose_env_file: Path | None,
        namespace: str,
        docker: Any,
        info: InfoFn,
        **_: object,
    ) -> dict[str, str]:
        resolved_namespace = _text(namespace) or "media-stack"
        stack_username = _text(compose_env.get("STACK_ADMIN_USERNAME")) or "admin"
        stack_password = _text(compose_env.get("STACK_ADMIN_PASSWORD")) or resolved_namespace
        compose_env["STACK_ADMIN_USERNAME"] = stack_username
        compose_env["STACK_ADMIN_PASSWORD"] = stack_password

        if compose_env_file is not None:
            updates: dict[str, str] = {}
            if _text(compose_env.get("STACK_ADMIN_USERNAME")):
                updates["STACK_ADMIN_USERNAME"] = stack_username
            if _text(compose_env.get("STACK_ADMIN_PASSWORD")):
                updates["STACK_ADMIN_PASSWORD"] = stack_password
            _upsert_env_file(Path(compose_env_file), updates)

        container = docker.get_container("qbittorrent")
        if container is None:
            info(
                "Compose torrent-client preflight: container 'qbittorrent' not found; "
                "skipping credential sync."
            )
            return {
                "STACK_ADMIN_USERNAME": stack_username,
                "STACK_ADMIN_PASSWORD": stack_password,
            }

        if _wait_for_login(container, stack_username, stack_password, timeout_seconds=30):
            info(
                "Compose torrent-client preflight: stack-admin credentials already valid "
                "for qBittorrent."
            )
            return {
                "STACK_ADMIN_USERNAME": stack_username,
                "STACK_ADMIN_PASSWORD": stack_password,
            }

        startup_password = _read_temporary_password(container, timeout_seconds=20)
        startup_username = _text(compose_env.get("QBIT_STARTUP_USERNAME")) or "admin"
        if not startup_password:
            if not _reset_auth_config_in_container(container):
                raise RuntimeError(
                    "Compose torrent-client preflight could not reset qBittorrent WebUI "
                    "auth configuration."
                )
            if not _restart_container(container):
                raise RuntimeError(
                    "Compose torrent-client preflight failed to restart qBittorrent after "
                    "auth reset."
                )
            startup_password = _read_temporary_password(container, timeout_seconds=60)
            if not startup_password:
                raise RuntimeError(
                    "Compose torrent-client preflight could not resolve qBittorrent "
                    "temporary password after auth reset. Ensure qBittorrent startup logs "
                    "are available."
                )
            info(
                "Compose torrent-client preflight: regenerated temporary qBittorrent "
                "startup credentials via auth reset."
            )
        if not _set_credentials_with_container(
            container,
            auth_user=startup_username,
            auth_pass=startup_password,
            target_user=stack_username,
            target_pass=stack_password,
        ):
            raise RuntimeError(
                "Compose torrent-client preflight could not authenticate with qBittorrent "
                "startup credentials."
            )
        if not _wait_for_login(container, stack_username, stack_password):
            raise RuntimeError(
                "Compose torrent-client preflight updated qBittorrent credentials but "
                "verification with stack-admin credentials failed."
            )

        info(
            "Compose torrent-client preflight: synchronized qBittorrent WebUI credentials "
            "to stack-admin credentials."
        )
        return {
            "STACK_ADMIN_USERNAME": stack_username,
            "STACK_ADMIN_PASSWORD": stack_password,
        }


_instance = QbittorrentComposePreflight()
ensure_compose_torrent_client_credentials = _instance.ensure_compose_torrent_client_credentials


__all__ = ["ensure_compose_torrent_client_credentials"]
_decode_logs = _instance._decode_logs
_exec_shell = _instance._exec_shell
_extract_temporary_password = _instance._extract_temporary_password
_login_with_container = _instance._login_with_container
_read_temporary_password = _instance._read_temporary_password
_reset_auth_config_in_container = _instance._reset_auth_config_in_container
_restart_container = _instance._restart_container
_set_credentials_with_container = _instance._set_credentials_with_container
_text = _instance._text
_upsert_env_file = _instance._upsert_env_file
_wait_for_login = _instance._wait_for_login
_wait_for_webui_ready = _instance._wait_for_webui_ready
