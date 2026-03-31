#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from urllib import error, parse, request

from cli.jellyfin_bootstrap_db_discovery_service import (
    discover_api_key_from_jellyfin_db,
)
from cli.jellyfin_bootstrap_kube_service import (
    PortForward,
    choose_kubectl,
    get_secret,
    patch_secret,
    pick_free_local_port,
)


def log(level, message):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{ts}] [{level}] {message}", flush=True)


def info(message):
    log("INFO", message)


def warn(message):
    log("WARN", message)


def fail(message):
    log("ERR", message)
    raise RuntimeError(message)


def http_request(base_url, path, method="GET", payload=None, headers=None, timeout=20):
    url = f"{base_url.rstrip('/')}{path}"
    body = None
    req_headers = {}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = request.Request(url=url, data=body, method=method, headers=req_headers)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = None
            if raw:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = raw
            return resp.status, parsed, raw
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        parsed = None
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
        return exc.code, parsed, raw
    except error.URLError as exc:
        return 0, None, str(exc)


def wait_for_jellyfin(base_url, timeout_seconds=180):
    start = time.time()
    while time.time() - start < timeout_seconds:
        status, data, _ = http_request(base_url, "/System/Info/Public", timeout=8)
        if status == 200 and isinstance(data, dict):
            return data
        time.sleep(2)
    fail(f"Timed out waiting for Jellyfin at {base_url}/System/Info/Public")


def can_authenticate_jellyfin(base_url, username, password):
    headers = {
        "X-Emby-Authorization": (
            'MediaBrowser Client="media-stack-bootstrap", Device="media-stack-bootstrap", '
            'DeviceId="media-stack-bootstrap", Version="1.0.0"'
        )
    }
    payload = {"Username": username, "Pw": password}
    status, data, _ = http_request(
        base_url,
        "/Users/AuthenticateByName",
        method="POST",
        payload=payload,
        headers=headers,
    )
    return bool(
        status == 200 and isinstance(data, dict) and str(data.get("AccessToken") or "").strip()
    )


def authenticate_with_credentials(base_url, username, password):
    headers = {
        "X-Emby-Authorization": (
            'MediaBrowser Client="media-stack-bootstrap", Device="media-stack-bootstrap", '
            'DeviceId="media-stack-bootstrap", Version="1.0.0"'
        )
    }
    payload = {"Username": username, "Pw": password}
    status, data, body = http_request(
        base_url,
        "/Users/AuthenticateByName",
        method="POST",
        payload=payload,
        headers=headers,
    )
    if status != 200 or not isinstance(data, dict):
        return None, status, body
    token = str(data.get("AccessToken") or "").strip()
    user = data.get("User") or {}
    user_id = str(user.get("Id") or "").strip()
    if not token:
        return None, status, "Authentication succeeded without access token."
    return {"token": token, "user_id": user_id, "username": str(username)}, status, body


def resolve_startup_username(base_url):
    for path in ("/Startup/FirstUser", "/Startup/User"):
        status, data, _ = http_request(base_url, path)
        if status == 200 and isinstance(data, dict):
            name = str(data.get("Name") or "").strip()
            if name:
                return name
    return ""


def try_authenticate_startup_user(base_url, preferred_password):
    startup_username = resolve_startup_username(base_url)
    if not startup_username:
        return None
    attempted_passwords = []
    for candidate in (preferred_password, ""):
        if candidate in attempted_passwords:
            continue
        attempted_passwords.append(candidate)
        auth, _, _ = authenticate_with_credentials(base_url, startup_username, candidate)
        if auth:
            auth["password_used"] = candidate
            return auth
    return None


def update_user_password(base_url, session_token, user_id, current_pw, new_pw):
    headers = {"X-Emby-Authorization": f'MediaBrowser Token="{session_token}"'}
    status, _, body = http_request(
        base_url,
        f"/Users/Password?userId={parse.quote(user_id, safe='')}",
        method="POST",
        payload={"CurrentPw": current_pw, "NewPw": new_pw},
        headers=headers,
    )
    if status not in (200, 201, 202, 204):
        raise RuntimeError(f"Jellyfin password update failed (HTTP {status}): {body}")


def startup_wizard_if_needed(base_url, username, password):
    info_public = wait_for_jellyfin(base_url)
    if bool(info_public.get("StartupWizardCompleted", False)):
        info("Jellyfin startup wizard already completed.")
        return

    info("Jellyfin startup wizard not completed; applying automated first-run setup.")
    config_payload = {
        "ServerName": "media-stack",
        "UICulture": "en-US",
        "MetadataCountryCode": "US",
        "PreferredMetadataLanguage": "en",
    }
    status, _, body = http_request(
        base_url, "/Startup/Configuration", method="POST", payload=config_payload
    )
    if status not in (200, 201, 202, 204):
        warn(f"Jellyfin startup config step returned HTTP {status}: {body}")

    status, _, body = http_request(
        base_url,
        "/Startup/User",
        method="POST",
        payload={"Name": username, "Password": password},
    )
    if status not in (200, 201, 202, 204):
        if can_authenticate_jellyfin(base_url, username, password):
            warn(
                f"Jellyfin startup user step returned HTTP {status}, but admin login works; "
                "continuing startup bootstrap."
            )
        else:
            warn(
                f"Jellyfin startup user setup failed (HTTP {status}) and stack-admin auth did not "
                "succeed. Continuing with API key discovery/recovery flow."
            )
            return

    status, _, body = http_request(
        base_url,
        "/Startup/RemoteAccess",
        method="POST",
        payload={"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False},
    )
    if status not in (200, 201, 202, 204):
        warn(f"Jellyfin startup remote-access step returned HTTP {status}: {body}")

    status, _, body = http_request(base_url, "/Startup/Complete", method="POST")
    if status not in (200, 201, 202, 204):
        warn(f"Jellyfin startup completion step returned HTTP {status}: {body}")

    for _ in range(30):
        info_public = wait_for_jellyfin(base_url, timeout_seconds=15)
        if bool(info_public.get("StartupWizardCompleted", False)):
            info("Jellyfin startup wizard completed successfully.")
            return
        time.sleep(1)

    if can_authenticate_jellyfin(base_url, username, password):
        warn(
            "Jellyfin startup wizard flag is still false, but admin authentication works. "
            "Proceeding with API key reconciliation."
        )
        return

    warn(
        "Jellyfin startup wizard still not completed after automation and stack-admin auth failed. "
        "Continuing with API key discovery/recovery flow."
    )
    return


def _authenticate_jellyfin(base_url, username, password):
    headers = {
        "X-Emby-Authorization": (
            'MediaBrowser Client="media-stack-bootstrap", Device="media-stack-bootstrap", '
            'DeviceId="media-stack-bootstrap", Version="1.0.0"'
        )
    }
    payload = {"Username": username, "Pw": password}
    status, data, body = http_request(
        base_url,
        "/Users/AuthenticateByName",
        method="POST",
        payload=payload,
        headers=headers,
    )
    if status != 200 or not isinstance(data, dict):
        return None, None, status, body
    token = str(data.get("AccessToken") or "").strip()
    user = data.get("User") or {}
    user_id = str(user.get("Id") or "").strip()
    if not token:
        return None, None, status, "Authentication succeeded without access token."
    return token, user_id, status, body


def authenticate_jellyfin(base_url, username, password):
    token, user_id, status, body = _authenticate_jellyfin(base_url, username, password)
    if not token:
        fail(f"Jellyfin authentication failed (HTTP {status}): {body}")
    info("Jellyfin authentication succeeded with stack admin credentials.")
    return token, user_id


def try_authenticate_jellyfin(base_url, username, password):
    token, user_id, status, body = _authenticate_jellyfin(base_url, username, password)
    if not token:
        warn(f"Jellyfin authentication with stack admin credentials failed (HTTP {status}).")
        return None
    info("Jellyfin authentication succeeded with stack admin credentials.")
    return token, user_id


def ensure_api_key(base_url, session_token, app_name):
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
        base_url, f"/Auth/Keys?app={app_q}", method="POST", headers=auth_header
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

    # fallback: use first key if app-specific matching fails
    if items:
        token = str(items[0].get("AccessToken") or "").strip()
        if token:
            warn(
                "Jellyfin API key for requested app was not found; using first available key from /Auth/Keys."
            )
            return token

    fail("No usable Jellyfin API key found after key creation.")


def validate_api_key(base_url, api_key):
    status, data, _ = http_request(base_url, f"/Users?api_key={parse.quote(api_key, safe='')}")
    return status == 200 and isinstance(data, list)


def lookup_user_id_with_api_key(base_url, api_key, preferred_username):
    status, data, body = http_request(base_url, f"/Users?api_key={parse.quote(api_key, safe='')}")
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="scripts/ensure-jellyfin-bootstrap.sh",
        description=(
            "Completes Jellyfin first-run bootstrap and syncs API key/user id into media-stack secret."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    parse_args(argv)
    namespace = os.environ.get("NAMESPACE", "media-stack")
    secret_name = os.environ.get("SECRET_NAME", "media-stack-secrets")
    service_name = os.environ.get("JELLYFIN_SERVICE_NAME", "jellyfin")
    wait_seconds = int(os.environ.get("JELLYFIN_BOOTSTRAP_WAIT_SECONDS", "180"))
    app_name = os.environ.get("JELLYFIN_API_KEY_APP_NAME", "media-stack-bootstrap")

    kubectl = choose_kubectl()
    info(f"Namespace: {namespace}")
    info(f"Secret: {secret_name}")
    info(f"Jellyfin service: {service_name}")

    secret = get_secret(kubectl, namespace, secret_name)
    stack_user = secret.get("STACK_ADMIN_USERNAME") or os.environ.get(
        "STACK_ADMIN_USERNAME", "mediaadmin"
    )
    stack_pass = secret.get("STACK_ADMIN_PASSWORD") or os.environ.get(
        "STACK_ADMIN_PASSWORD", "media-stack-admin"
    )
    existing_api_key = secret.get("JELLYFIN_API_KEY", "").strip()
    existing_user_id = secret.get("JELLYFIN_USER_ID", "").strip()

    local_port = pick_free_local_port()
    pf_cmd = kubectl + [
        "-n",
        namespace,
        "port-forward",
        f"svc/{service_name}",
        f"{local_port}:8096",
    ]
    base_url = f"http://127.0.0.1:{local_port}"
    info(f"Using local Jellyfin endpoint: {base_url}")

    with PortForward(pf_cmd) as pf:
        # wait for initial readiness
        started = False
        start = time.time()
        while time.time() - start < wait_seconds:
            pf.ensure_alive()
            status, data, _ = http_request(base_url, "/System/Info/Public", timeout=8)
            if status == 200 and isinstance(data, dict):
                started = True
                break
            time.sleep(2)
        if not started:
            fail("Timed out waiting for Jellyfin port-forward endpoint readiness.")

        startup_wizard_if_needed(base_url, stack_user, stack_pass)

        # If existing key already works, keep it unless we need to refresh user id.
        if existing_api_key and validate_api_key(base_url, existing_api_key):
            info("Existing Jellyfin API key from secret is valid.")
            if existing_user_id:
                info("Jellyfin bootstrap already satisfied.")
                return
            user_id = lookup_user_id_with_api_key(base_url, existing_api_key, stack_user)
            if user_id:
                patch_secret(
                    kubectl,
                    namespace,
                    secret_name,
                    {"JELLYFIN_USER_ID": user_id},
                )
                info("Updated media-stack secret with Jellyfin user id.")
                return
            warn(
                "Existing Jellyfin API key is valid but user id could not be discovered; leaving current secret values."
            )
            return

        auth_result = try_authenticate_jellyfin(base_url, stack_user, stack_pass)
        if auth_result is None:
            startup_auth = try_authenticate_startup_user(base_url, stack_pass)
            if startup_auth:
                startup_user = startup_auth.get("username", "root")
                startup_user_id = startup_auth.get("user_id", "")
                startup_password_used = startup_auth.get("password_used", "")
                startup_token = startup_auth.get("token", "")
                info(
                    "Authenticated using Jellyfin startup user fallback "
                    f"(username={startup_user})."
                )
                if (
                    startup_token
                    and startup_user_id
                    and stack_pass
                    and startup_password_used != stack_pass
                ):
                    try:
                        update_user_password(
                            base_url,
                            startup_token,
                            startup_user_id,
                            startup_password_used,
                            stack_pass,
                        )
                        info(
                            "Updated Jellyfin startup-user password to match STACK_ADMIN_PASSWORD."
                        )
                        upgraded, _, _ = authenticate_with_credentials(
                            base_url, startup_user, stack_pass
                        )
                        if upgraded:
                            startup_token = upgraded["token"]
                            startup_user_id = upgraded["user_id"]
                    except Exception as exc:
                        warn(f"Could not rotate Jellyfin startup-user password: {exc}")

                api_key = ensure_api_key(base_url, startup_token, app_name)
                if not validate_api_key(base_url, api_key):
                    fail("Generated Jellyfin API key failed validation against /Users.")
                patch_secret(
                    kubectl,
                    namespace,
                    secret_name,
                    {"JELLYFIN_API_KEY": api_key, "JELLYFIN_USER_ID": startup_user_id},
                )
                if str(startup_user).strip().lower() != str(stack_user).strip().lower():
                    warn(
                        "Jellyfin fallback authenticated a different username "
                        f"('{startup_user}') than STACK_ADMIN_USERNAME ('{stack_user}'). "
                        "If desired, align STACK_ADMIN_USERNAME to avoid future auth warnings."
                    )
                info("Updated media-stack secret with recovered Jellyfin API key and user id.")
                return

            if existing_api_key and validate_api_key(base_url, existing_api_key):
                warn(
                    "Stack admin login failed, but existing Jellyfin API key is valid. "
                    "Keeping existing API key in secret."
                )
                if not existing_user_id:
                    user_id = lookup_user_id_with_api_key(base_url, existing_api_key, stack_user)
                    if user_id:
                        patch_secret(
                            kubectl,
                            namespace,
                            secret_name,
                            {"JELLYFIN_USER_ID": user_id},
                        )
                        info("Updated media-stack secret with Jellyfin user id.")
                return
            info("Attempting Jellyfin API key auto-discovery from /config/data/jellyfin.db.")
            discovered_key, discovered_user_id = discover_api_key_from_jellyfin_db(
                kubectl,
                namespace,
                service_name,
                [app_name, "Jellyfin", "Jellyseerr", "media-stack-bootstrap"],
                stack_user,
                warn=warn,
            )
            if discovered_key and validate_api_key(base_url, discovered_key):
                patch_payload = {"JELLYFIN_API_KEY": discovered_key}
                if discovered_user_id:
                    patch_payload["JELLYFIN_USER_ID"] = discovered_user_id
                patch_secret(kubectl, namespace, secret_name, patch_payload)
                info("Recovered Jellyfin API key from DB and updated secret.")
                return
            fail(
                "Jellyfin bootstrap could not authenticate with stack admin credentials and no valid API key could be recovered. "
                "If Jellyfin was previously initialized with different credentials, set JELLYFIN_API_KEY manually "
                "using scripts/set-jellyfin-api-key.sh, then rerun bootstrap."
            )

        session_token, user_id = auth_result
        api_key = ensure_api_key(base_url, session_token, app_name)
        if not validate_api_key(base_url, api_key):
            fail("Generated Jellyfin API key failed validation against /System/Info.")

        patch_secret(
            kubectl,
            namespace,
            secret_name,
            {"JELLYFIN_API_KEY": api_key, "JELLYFIN_USER_ID": user_id},
        )
        info("Updated media-stack secret with Jellyfin API key and user id.")

    info("Jellyfin bootstrap/key automation complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("ERR", str(exc))
        sys.exit(1)
