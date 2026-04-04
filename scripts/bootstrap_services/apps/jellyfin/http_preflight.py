"""Jellyfin preflight: startup wizard, auth, and API key provisioning via HTTP.

Replaces the compose_preflight.py docker-exec-based approach with pure HTTP
calls over the Docker network (http://jellyfin:8096).
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request


def _http(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
) -> tuple[int, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body)
            except (json.JSONDecodeError, ValueError):
                return resp.status, body
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, body
    except Exception:
        return 0, None


def _wait_ready(base_url: str, timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, _ = _http(base_url, "/System/Info/Public")
        if status == 200:
            return True
        time.sleep(3)
    return False


def run_preflight(
    *,
    jellyfin_url: str = "http://jellyfin:8096",
    admin_username: str = "admin",
    admin_password: str = "media-dev",
    api_key_name: str = "media-stack-bootstrap",
    wait_timeout: int = 120,
    log: Any = None,
    **kwargs: Any,
) -> dict[str, str]:
    """Run Jellyfin startup wizard + API key provisioning.

    Returns dict with JELLYFIN_API_KEY and JELLYFIN_USER_ID if successful.
    """

    def info(msg: str) -> None:
        if log:
            log(msg)

    info(f"Jellyfin preflight: waiting for {jellyfin_url}")
    if not _wait_ready(jellyfin_url, timeout=wait_timeout):
        raise RuntimeError(f"Jellyfin not reachable at {jellyfin_url} within {wait_timeout}s")

    # Check if startup wizard is needed.
    status, data = _http(jellyfin_url, "/Startup/Configuration")
    if status == 200:
        info("Jellyfin startup wizard detected — completing initial setup")
        _http(jellyfin_url, "/Startup/Configuration", method="POST", payload={
            "UICulture": "en-US",
            "MetadataCountryCode": "US",
            "PreferredMetadataLanguage": "en",
        })
        _http(jellyfin_url, "/Startup/User", method="POST", payload={
            "Name": admin_username,
            "Password": admin_password,
        })
        _http(jellyfin_url, "/Startup/RemoteAccess", method="POST", payload={
            "EnableRemoteAccess": True,
            "EnableAutomaticPortMapping": False,
        })
        _http(jellyfin_url, "/Startup/Complete", method="POST")
        info("Jellyfin startup wizard completed")
        # Give Jellyfin time to initialize after wizard completion.
        import time as _time

        _time.sleep(3)

    # Authenticate.
    status, auth_data = _http(jellyfin_url, "/Users/AuthenticateByName", method="POST", payload={
        "Username": admin_username,
        "Pw": admin_password,
    }, headers={"X-Emby-Authorization": 'MediaBrowser Client="Bootstrap", Device="Server", DeviceId="bootstrap", Version="1.0"'})

    if status != 200 or not isinstance(auth_data, dict):
        raise RuntimeError(f"Jellyfin authentication failed (HTTP {status})")

    access_token = auth_data.get("AccessToken", "")
    user_id = auth_data.get("User", {}).get("Id", "")
    info(f"Jellyfin authentication succeeded (user_id={user_id})")

    # Check for existing API key.
    auth_header = {"X-Emby-Token": access_token}
    status, keys_data = _http(jellyfin_url, "/Auth/Keys", headers=auth_header)
    existing_key = ""
    if status == 200 and isinstance(keys_data, dict):
        for item in keys_data.get("Items", []):
            if isinstance(item, dict) and item.get("AppName") == api_key_name:
                existing_key = item.get("AccessToken", "")
                break

    if existing_key:
        info(f"Jellyfin API key already exists for app '{api_key_name}'")
        return {"JELLYFIN_API_KEY": existing_key, "JELLYFIN_USER_ID": user_id}

    # Create new API key.
    status, _ = _http(
        jellyfin_url,
        f"/Auth/Keys?app={api_key_name}",
        method="POST",
        headers=auth_header,
    )
    if status not in (200, 204):
        raise RuntimeError(f"Jellyfin API key creation failed (HTTP {status})")

    # Re-fetch to get the key value.
    status, keys_data = _http(jellyfin_url, "/Auth/Keys", headers=auth_header)
    api_key = ""
    if status == 200 and isinstance(keys_data, dict):
        for item in keys_data.get("Items", []):
            if isinstance(item, dict) and item.get("AppName") == api_key_name:
                api_key = item.get("AccessToken", "")
                break

    if not api_key:
        raise RuntimeError("Jellyfin API key created but could not be retrieved")

    info(f"Jellyfin API key created for app '{api_key_name}'")
    return {"JELLYFIN_API_KEY": api_key, "JELLYFIN_USER_ID": user_id}
