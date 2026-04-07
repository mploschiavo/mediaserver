from __future__ import annotations

from urllib import parse


def ensure_api_key(
    base_url: str,
    session_token: str,
    app_name: str,
    *,
    http_request,
    info,
    warn,
    fail,
) -> str:
    auth_header = {"X-Emby-Authorization": f'MediaBrowser Token="{session_token}"'}

    status, data, body = http_request(base_url, "/Auth/Keys", headers=auth_header)
    if status != 200 or not isinstance(data, dict):
        fail(f"Jellyfin key list failed (HTTP {status}): {body}")
    items = data.get("Items") or []
    for item in items:
        if str(item.get("AppName") or "").strip().lower() == app_name.lower():
            token = str(item.get("AccessToken") or "").strip()
            if token:
                info(f"Jellyfin API key already exists for app '{app_name}'.")
                return token

    app_q = parse.quote(app_name, safe="")
    status, _, body = http_request(
        base_url,
        f"/Auth/Keys?app={app_q}",
        method="POST",
        headers=auth_header,
    )
    if status not in (200, 201, 202, 204):
        fail(f"Jellyfin key create failed (HTTP {status}): {body}")

    status, data, body = http_request(base_url, "/Auth/Keys", headers=auth_header)
    if status != 200 or not isinstance(data, dict):
        fail(f"Jellyfin key list after create failed (HTTP {status}): {body}")
    items = data.get("Items") or []
    for item in items:
        if str(item.get("AppName") or "").strip().lower() == app_name.lower():
            token = str(item.get("AccessToken") or "").strip()
            if token:
                info(f"Jellyfin API key created for app '{app_name}'.")
                return token

    if items:
        token = str(items[0].get("AccessToken") or "").strip()
        if token:
            warn(
                "Jellyfin API key for requested app was not found; using first available key from /Auth/Keys."
            )
            return token

    fail("No usable Jellyfin API key found after key creation.")
    return ""


def validate_api_key(base_url: str, api_key: str, *, http_request) -> bool:
    status, data, _ = http_request(base_url, f"/Users?api_key={parse.quote(api_key, safe='')}")
    return status == 200 and isinstance(data, list)


def lookup_user_id_with_api_key(
    base_url: str,
    api_key: str,
    preferred_username: str,
    *,
    http_request,
) -> str:
    status, data, _ = http_request(base_url, f"/Users?api_key={parse.quote(api_key, safe='')}")
    if status != 200 or not isinstance(data, list):
        return ""
    preferred = str(preferred_username or "").strip().lower()
    for user in data:
        if not isinstance(user, dict):
            continue
        name = str(user.get("Name") or "").strip().lower()
        uid = str(user.get("Id") or "").strip()
        if preferred and name == preferred and uid:
            return uid
    for user in data:
        if not isinstance(user, dict):
            continue
        if bool(user.get("Policy", {}).get("IsAdministrator", False)):
            uid = str(user.get("Id") or "").strip()
            if uid:
                return uid
    return ""
