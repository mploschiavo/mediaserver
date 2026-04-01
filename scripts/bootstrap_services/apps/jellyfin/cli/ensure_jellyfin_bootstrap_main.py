#!/usr/bin/env python3
import json
import sys
import time
from urllib import error, request

from .jellyfin_bootstrap_api_key_service import (
    ensure_api_key,
    lookup_user_id_with_api_key,
    validate_api_key,
)
from .jellyfin_bootstrap_auth_service import JellyfinBootstrapAuthService
from .jellyfin_bootstrap_config_service import parse_jellyfin_bootstrap_config
from .jellyfin_bootstrap_db_discovery_service import (
    discover_api_key_from_jellyfin_db,
)
from .jellyfin_bootstrap_kube_service import (
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


def main(argv=None):
    cfg = parse_jellyfin_bootstrap_config(argv)
    namespace = cfg.namespace
    secret_name = cfg.secret_name
    service_name = cfg.service_name
    wait_seconds = cfg.wait_seconds
    app_name = cfg.app_name

    kubectl = choose_kubectl()
    info(f"Namespace: {namespace}")
    info(f"Secret: {secret_name}")
    info(f"Jellyfin service: {service_name}")

    secret = get_secret(kubectl, namespace, secret_name)
    stack_user = str(secret.get("STACK_ADMIN_USERNAME") or "").strip()
    stack_pass = str(secret.get("STACK_ADMIN_PASSWORD") or "").strip()
    if not stack_user:
        fail(
            "Missing STACK_ADMIN_USERNAME in secret and environment. "
            "Set it in media-stack-secrets before running Jellyfin bootstrap."
        )
    if not stack_pass:
        fail(
            "Missing STACK_ADMIN_PASSWORD in secret and environment. "
            "Set it in media-stack-secrets before running Jellyfin bootstrap."
        )
    if stack_pass == "change-me":
        fail(
            "STACK_ADMIN_PASSWORD is 'change-me'. Set a real password in media-stack-secrets first."
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
    auth_service = JellyfinBootstrapAuthService(
        http_request=http_request,
        info=info,
        warn=warn,
        fail=fail,
    )

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

        auth_service.startup_wizard_if_needed(base_url, stack_user, stack_pass)

        # If existing key already works, keep it unless we need to refresh user id.
        if existing_api_key and validate_api_key(
            base_url, existing_api_key, http_request=http_request
        ):
            info("Existing Jellyfin API key from secret is valid.")
            if existing_user_id:
                info("Jellyfin bootstrap already satisfied.")
                return
            user_id = lookup_user_id_with_api_key(
                base_url,
                existing_api_key,
                stack_user,
                http_request=http_request,
            )
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

        auth_result = auth_service.try_authenticate_jellyfin(base_url, stack_user, stack_pass)
        if auth_result is None:
            startup_auth = auth_service.try_authenticate_startup_user(base_url, stack_pass)
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
                        auth_service.update_user_password(
                            base_url,
                            startup_token,
                            startup_user_id,
                            startup_password_used,
                            stack_pass,
                        )
                        info(
                            "Updated Jellyfin startup-user password to match STACK_ADMIN_PASSWORD."
                        )
                        upgraded, _, _ = auth_service.authenticate_with_credentials(
                            base_url, startup_user, stack_pass
                        )
                        if upgraded:
                            startup_token = upgraded["token"]
                            startup_user_id = upgraded["user_id"]
                    except Exception as exc:
                        warn(f"Could not rotate Jellyfin startup-user password: {exc}")

                api_key = ensure_api_key(
                    base_url,
                    startup_token,
                    app_name,
                    http_request=http_request,
                    info=info,
                    warn=warn,
                    fail=fail,
                )
                if not validate_api_key(base_url, api_key, http_request=http_request):
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

            if existing_api_key and validate_api_key(
                base_url, existing_api_key, http_request=http_request
            ):
                warn(
                    "Stack admin login failed, but existing Jellyfin API key is valid. "
                    "Keeping existing API key in secret."
                )
                if not existing_user_id:
                    user_id = lookup_user_id_with_api_key(
                        base_url,
                        existing_api_key,
                        stack_user,
                        http_request=http_request,
                    )
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
            if discovered_key and validate_api_key(
                base_url, discovered_key, http_request=http_request
            ):
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
        api_key = ensure_api_key(
            base_url,
            session_token,
            app_name,
            http_request=http_request,
            info=info,
            warn=warn,
            fail=fail,
        )
        if not validate_api_key(base_url, api_key, http_request=http_request):
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
