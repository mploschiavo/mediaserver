#!/usr/bin/env python3
import base64
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib import error, parse, request


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


def choose_kubectl():
    if shutil.which("microk8s"):
        return ["microk8s", "kubectl"]
    if shutil.which("kubectl"):
        return ["kubectl"]
    fail("Neither 'microk8s' nor 'kubectl' is available in PATH.")


def run_cmd(cmd, check=True):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def get_secret(kubectl, namespace, secret_name):
    proc = run_cmd(
        kubectl + ["-n", namespace, "get", "secret", secret_name, "-o", "json"], check=False
    )
    if proc.returncode != 0:
        return {}
    raw = json.loads(proc.stdout)
    data = raw.get("data") or {}
    decoded = {}
    for key, value in data.items():
        try:
            decoded[key] = base64.b64decode(value).decode("utf-8")
        except Exception:
            decoded[key] = ""
    return decoded


def patch_secret(kubectl, namespace, secret_name, values):
    patch = {"stringData": values}
    run_cmd(
        kubectl
        + [
            "-n",
            namespace,
            "patch",
            "secret",
            secret_name,
            "--type",
            "merge",
            "-p",
            json.dumps(patch),
        ]
    )


def pick_free_local_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class PortForward:
    def __init__(self, cmd):
        self.cmd = cmd
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid,
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    pass

    def ensure_alive(self):
        if self.proc and self.proc.poll() is not None:
            out = ""
            err = ""
            try:
                out = self.proc.stdout.read() if self.proc.stdout else ""
            except Exception:
                pass
            try:
                err = self.proc.stderr.read() if self.proc.stderr else ""
            except Exception:
                pass
            raise RuntimeError(
                f"kubectl port-forward exited early (code={self.proc.returncode}). stdout={out} stderr={err}"
            )


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
        fail(f"Jellyfin startup config failed (HTTP {status}): {body}")

    status, _, body = http_request(
        base_url,
        "/Startup/User",
        method="POST",
        payload={"Name": username, "Password": password},
    )
    if status not in (200, 201, 202, 204):
        fail(f"Jellyfin startup user setup failed (HTTP {status}): {body}")

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
        fail(f"Jellyfin startup completion failed (HTTP {status}): {body}")

    for _ in range(30):
        info_public = wait_for_jellyfin(base_url, timeout_seconds=15)
        if bool(info_public.get("StartupWizardCompleted", False)):
            info("Jellyfin startup wizard completed successfully.")
            return
        time.sleep(1)

    fail("Jellyfin startup wizard still not completed after automation.")


def authenticate_jellyfin(base_url, username, password):
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
        fail(f"Jellyfin authentication failed (HTTP {status}): {body}")

    token = str(data.get("AccessToken") or "").strip()
    user = data.get("User") or {}
    user_id = str(user.get("Id") or "").strip()
    if not token:
        fail("Jellyfin authentication succeeded but no AccessToken was returned.")
    info("Jellyfin authentication succeeded with stack admin credentials.")
    return token, user_id


def try_authenticate_jellyfin(base_url, username, password):
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
        warn(
            f"Jellyfin authentication with stack admin credentials failed (HTTP {status})."
        )
        return None

    token = str(data.get("AccessToken") or "").strip()
    user = data.get("User") or {}
    user_id = str(user.get("Id") or "").strip()
    if not token:
        warn("Jellyfin authentication returned no token.")
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
    status, data, _ = http_request(base_url, f"/System/Info?api_key={parse.quote(api_key, safe='')}")
    return status == 200 and isinstance(data, dict)


def lookup_user_id_with_api_key(base_url, api_key, preferred_username):
    status, data, body = http_request(
        base_url, f"/Users?api_key={parse.quote(api_key, safe='')}"
    )
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


def discover_api_key_from_jellyfin_db(
    kubectl, namespace, service_name, preferred_app_names, preferred_username
):
    pod_proc = run_cmd(
        kubectl
        + [
            "-n",
            namespace,
            "get",
            "pods",
            "-l",
            f"app={service_name}",
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ],
        check=False,
    )
    pod_name = str(pod_proc.stdout or "").strip()
    if pod_proc.returncode != 0 or not pod_name:
        warn("Could not resolve Jellyfin pod for DB key discovery.")
        return "", ""

    fd, local_db = tempfile.mkstemp(prefix="jellyfin-db-", suffix=".sqlite")
    os.close(fd)
    try:
        cp_proc = run_cmd(
            kubectl
            + [
                "-n",
                namespace,
                "cp",
                f"{pod_name}:/config/data/jellyfin.db",
                local_db,
            ],
            check=False,
        )
        if cp_proc.returncode != 0:
            warn("Failed copying jellyfin.db from pod for key discovery.")
            return "", ""

        con = sqlite3.connect(local_db)
        cur = con.cursor()
        preferred = [str(x).strip().lower() for x in preferred_app_names if str(x).strip()]
        discovered_key = ""
        for app_name in preferred:
            cur.execute(
                "SELECT AccessToken FROM ApiKeys WHERE lower(Name)=? ORDER BY Id DESC LIMIT 1",
                (app_name,),
            )
            row = cur.fetchone()
            if row and str(row[0] or "").strip():
                discovered_key = str(row[0]).strip()
                break
        if not discovered_key:
            cur.execute(
                "SELECT AccessToken FROM ApiKeys WHERE AccessToken IS NOT NULL AND AccessToken != '' ORDER BY Id DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row and str(row[0] or "").strip():
                discovered_key = str(row[0]).strip()

        discovered_user_id = ""
        preferred_user = str(preferred_username or "").strip().lower()
        if preferred_user:
            cur.execute(
                "SELECT Id FROM Users WHERE lower(Username)=? ORDER BY Id LIMIT 1",
                (preferred_user,),
            )
            row = cur.fetchone()
            if row and str(row[0] or "").strip():
                discovered_user_id = str(row[0]).strip()
        if not discovered_user_id:
            cur.execute("SELECT Id FROM Users ORDER BY Id LIMIT 1")
            row = cur.fetchone()
            if row and str(row[0] or "").strip():
                discovered_user_id = str(row[0]).strip()

        con.close()
        return discovered_key, discovered_user_id
    except Exception as exc:
        warn(f"Jellyfin DB key discovery failed: {exc}")
        return "", ""
    finally:
        try:
            os.remove(local_db)
        except Exception:
            pass


def main():
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
    stack_user = secret.get("STACK_ADMIN_USERNAME") or os.environ.get("STACK_ADMIN_USERNAME", "mediaadmin")
    stack_pass = secret.get("STACK_ADMIN_PASSWORD") or os.environ.get("STACK_ADMIN_PASSWORD", "media-stack-admin")
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
            if existing_api_key and validate_api_key(base_url, existing_api_key):
                warn(
                    "Stack admin login failed, but existing Jellyfin API key is valid. "
                    "Keeping existing API key in secret."
                )
                if not existing_user_id:
                    user_id = lookup_user_id_with_api_key(
                        base_url, existing_api_key, stack_user
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
