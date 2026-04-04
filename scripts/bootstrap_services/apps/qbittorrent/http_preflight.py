"""qBittorrent preflight: credential sync via HTTP API + config file I/O.

Replaces the compose_preflight.py docker-exec-based approach. Uses:
- HTTP requests to http://qbittorrent:8080 for login/preference setting
- Direct file I/O to /srv-config/qbittorrent/ for config manipulation
- Docker SDK only for reading container logs (temp password extraction)
"""

from __future__ import annotations

import configparser
import re
import time
from pathlib import Path
from typing import Any

import requests


def _wait_ready(base_url: str, timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"{base_url}/api/v2/app/version", timeout=5)
            if resp.status_code == 200:
                return True
        except requests.ConnectionError:
            pass
        time.sleep(3)
    return False


def _login(base_url: str, username: str, password: str) -> str | None:
    """Login and return session cookie (SID), or None on failure."""
    try:
        resp = requests.post(
            f"{base_url}/api/v2/auth/login",
            data={"username": username, "password": password},
            timeout=10,
        )
        if resp.status_code == 200 and "Ok" in resp.text:
            return resp.cookies.get("SID", "")
        return None
    except requests.ConnectionError:
        return None


def _set_preferences(base_url: str, sid: str, prefs: dict[str, Any]) -> bool:
    import json

    try:
        resp = requests.post(
            f"{base_url}/api/v2/app/setPreferences",
            data={"json": json.dumps(prefs)},
            cookies={"SID": sid},
            timeout=10,
        )
        return resp.status_code == 200
    except requests.ConnectionError:
        return False


def _read_temp_password_from_logs(container_name: str = "qbittorrent") -> str | None:
    """Extract temporary password from qBittorrent container logs via Docker SDK."""
    try:
        import docker

        client = docker.from_env()
        container = client.containers.get(container_name)
        log_text = container.logs(tail=100).decode("utf-8", errors="replace")
        match = re.search(
            r"temporary password.*?:\s*(\S+)",
            log_text,
            flags=re.IGNORECASE,
        )
        return match.group(1) if match else None
    except Exception:
        return None


def _reset_auth_in_config(config_root: Path) -> bool:
    """Reset auth settings in qBittorrent.conf by editing the file directly.

    This replaces the docker exec + sed approach. The bootstrap runner mounts
    CONFIG_ROOT at /srv-config, so the file is at /srv-config/qbittorrent/qBittorrent/qBittorrent.conf.
    """
    conf_path = config_root / "qbittorrent" / "qBittorrent" / "qBittorrent.conf"
    if not conf_path.exists():
        return False

    text = conf_path.read_text(encoding="utf-8", errors="replace")
    keys_to_remove = [
        r"WebUI\\Username",
        r"WebUI\\Password_PBKDF2",
        r"WebUI\\Password_ha1",
        r"WebUI\\LocalHostAuth",
        r"WebUI\\MaxAuthenticationFailCount",
        r"WebUI\\BanDuration",
    ]
    lines = text.splitlines()
    filtered = [
        line
        for line in lines
        if not any(re.match(rf"^\s*{key}\s*=", line) for key in keys_to_remove)
    ]
    new_text = "\n".join(filtered) + "\n"
    if new_text != text:
        conf_path.write_text(new_text, encoding="utf-8")
        return True
    return False


def _restart_container(container_name: str = "qbittorrent") -> None:
    """Restart qBittorrent container via Docker SDK."""
    try:
        import docker

        client = docker.from_env()
        container = client.containers.get(container_name)
        container.restart(timeout=15)
    except Exception as exc:
        raise RuntimeError(f"Failed to restart {container_name}: {exc}") from exc


def run_preflight(
    *,
    qbit_url: str = "http://qbittorrent:8080",
    admin_username: str = "admin",
    admin_password: str = "media-dev",
    config_root: str = "/srv-config",
    container_name: str = "qbittorrent",
    wait_timeout: int = 60,
    log: Any = None,
    **kwargs: Any,
) -> dict[str, str]:
    """Sync qBittorrent credentials to match stack admin creds.

    Returns empty dict (no env vars to propagate).
    """

    def info(msg: str) -> None:
        if log:
            log(msg)

    info(f"qBittorrent preflight: waiting for {qbit_url}")
    if not _wait_ready(qbit_url, timeout=wait_timeout):
        raise RuntimeError(f"qBittorrent not reachable at {qbit_url} within {wait_timeout}s")

    # Try logging in with desired credentials first.
    sid = _login(qbit_url, admin_username, admin_password)
    if sid is not None:
        info("qBittorrent: stack-admin credentials already valid")
        return {}

    # Try with default credentials.
    sid = _login(qbit_url, "admin", "adminadmin")
    if sid is None:
        # Try reading the temporary password from container logs.
        temp_pass = _read_temp_password_from_logs(container_name)
        if temp_pass:
            info(f"qBittorrent: trying temporary password from logs")
            sid = _login(qbit_url, "admin", temp_pass)

    if sid is None:
        # Last resort: reset auth config and restart.
        info("qBittorrent: resetting auth config and restarting")
        changed = _reset_auth_in_config(Path(config_root))
        if changed:
            _restart_container(container_name)
            if not _wait_ready(qbit_url, timeout=wait_timeout):
                raise RuntimeError("qBittorrent not reachable after auth reset + restart")
            # After reset, try default creds again.
            temp_pass = _read_temp_password_from_logs(container_name)
            if temp_pass:
                sid = _login(qbit_url, "admin", temp_pass)
            if sid is None:
                sid = _login(qbit_url, "admin", "adminadmin")

    if sid is None:
        raise RuntimeError("qBittorrent: unable to authenticate with any known credentials")

    # Set stack admin credentials.
    success = _set_preferences(qbit_url, sid, {
        "web_ui_username": admin_username,
        "web_ui_password": admin_password,
    })
    if not success:
        raise RuntimeError("qBittorrent: failed to update credentials via API")

    info(f"qBittorrent: credentials synced to stack admin ({admin_username})")
    return {}
