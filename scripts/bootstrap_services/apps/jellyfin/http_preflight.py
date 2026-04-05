"""Jellyfin preflight: startup wizard, auth, and API key provisioning.

Uses the same JellyfinBootstrapAuthService as the compose preflight
for exact parity. Runs inside the bootstrap runner container with
HTTP access to jellyfin:8096 and file access to /srv-config/jellyfin.
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
) -> tuple[int, Any, str]:
    """HTTP request returning (status, parsed_data, body_str) 3-tuple."""
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body), body
            except (json.JSONDecodeError, ValueError):
                return resp.status, body, body
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, None, body
    except Exception:
        return 0, None, ""


def _wait_ready(base_url: str, timeout_seconds: int = 120) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status, _, _ = _http(base_url, "/System/Info/Public")
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

    Uses JellyfinBootstrapAuthService for exact parity with compose preflight.
    Returns dict with JELLYFIN_API_KEY and JELLYFIN_USER_ID if successful.
    """

    def info(msg: str) -> None:
        if log:
            log(msg)

    info(f"Jellyfin preflight: waiting for {jellyfin_url}")
    if not _wait_ready(jellyfin_url, timeout_seconds=wait_timeout):
        raise RuntimeError(f"Jellyfin not reachable at {jellyfin_url} within {wait_timeout}s")

    # Use the SAME auth service as the compose preflight.
    from .cli.jellyfin_bootstrap_auth_service import JellyfinBootstrapAuthService

    auth_service = JellyfinBootstrapAuthService(
        http_request=_http,
        info=info,
        warn=info,
        fail=lambda msg: (_ for _ in ()).throw(RuntimeError(msg)),
    )

    # Step 1: Complete wizard if needed.
    auth_service.startup_wizard_if_needed(
        jellyfin_url,
        username=admin_username,
        password=admin_password,
    )

    # Step 2: Authenticate — try stack admin, then startup user with empty password.
    startup_auth = auth_service.try_authenticate_startup_user(jellyfin_url, admin_password)
    if startup_auth is None:
        raise RuntimeError("Jellyfin: could not authenticate after wizard completion")

    access_token = str(startup_auth.get("token", ""))
    user_id = str(startup_auth.get("user_id", ""))
    password_used = str(startup_auth.get("password_used", ""))

    # Step 3: Rotate password if needed.
    if password_used != admin_password:
        info(f"Jellyfin: rotating password to stack admin credentials")
        auth_service.update_user_password(
            jellyfin_url, access_token, user_id, password_used, admin_password,
        )
        startup_auth = auth_service.try_authenticate_startup_user(jellyfin_url, admin_password)
        if startup_auth is None:
            raise RuntimeError("Jellyfin: authentication failed after password rotation")
        access_token = str(startup_auth.get("token", ""))
        user_id = str(startup_auth.get("user_id", ""))

    info(f"Jellyfin: authenticated as user_id={user_id}")

    # Step 4: Create or find API key.
    auth_header = {"X-Emby-Token": access_token}
    _, keys_data, _ = _http(jellyfin_url, "/Auth/Keys", headers=auth_header)
    existing_key = ""
    if isinstance(keys_data, dict):
        for item in keys_data.get("Items", []):
            if isinstance(item, dict) and item.get("AppName") == api_key_name:
                existing_key = item.get("AccessToken", "")
                break

    if existing_key:
        info(f"Jellyfin: API key already exists for '{api_key_name}'")
        return {"JELLYFIN_API_KEY": existing_key, "JELLYFIN_USER_ID": user_id}

    # Create new API key.
    _http(jellyfin_url, f"/Auth/Keys?app={api_key_name}", method="POST", headers=auth_header)

    # Re-fetch to get the key value.
    _, keys_data, _ = _http(jellyfin_url, "/Auth/Keys", headers=auth_header)
    api_key = ""
    if isinstance(keys_data, dict):
        for item in keys_data.get("Items", []):
            if isinstance(item, dict) and item.get("AppName") == api_key_name:
                api_key = item.get("AccessToken", "")
                break

    if not api_key:
        raise RuntimeError("Jellyfin: API key created but could not be retrieved")

    info(f"Jellyfin: API key created for '{api_key_name}'")
    return {"JELLYFIN_API_KEY": api_key, "JELLYFIN_USER_ID": user_id}
