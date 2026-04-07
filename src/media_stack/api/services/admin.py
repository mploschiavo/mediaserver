"""Admin services: API key rotation, password reset, service restart.

All operations are driven by the service registry — no hardcoded app
names or paths. To support a new service, add its ServiceDef to registry.py.
"""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path
from typing import Any

from .registry import SERVICES, SERVICE_MAP, get_services_with_api_keys, get_services_with_password_api, get_services_with_password_config


# ---------------------------------------------------------------------------
# Key reading/writing helpers — keyed by api_key_format from registry
# ---------------------------------------------------------------------------

def _read_key_xml(path: Path) -> str:
    if not path.is_file():
        return ""
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", path.read_text(encoding="utf-8"))
    return m.group(1).strip() if m else ""


def _write_key_xml(path: Path, new_key: str) -> None:
    content = path.read_text(encoding="utf-8")
    content = re.sub(r"<ApiKey>[^<]*</ApiKey>", f"<ApiKey>{new_key}</ApiKey>", content)
    path.write_text(content, encoding="utf-8")


def _read_key_ini(path: Path) -> str:
    if not path.is_file():
        return ""
    m = re.search(r"^\s*api_key\s*=\s*(\S+)", path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).strip() if m else ""


def _write_key_ini(path: Path, new_key: str) -> None:
    content = path.read_text(encoding="utf-8")
    content = re.sub(r"^api_key\s*=\s*.*$", f"api_key = {new_key}", content, count=1, flags=re.MULTILINE)
    path.write_text(content, encoding="utf-8")


def _read_key_yaml(path: Path) -> str:
    if not path.is_file():
        return ""
    m = re.search(r"^\s*apikey:\s*['\"]?(\S+?)['\"]?\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).strip() if m else ""


def _write_key_yaml(path: Path, new_key: str) -> None:
    import yaml
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("auth", {})["apikey"] = new_key
    with open(path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)


def _read_key_json(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str((data.get("main") or {}).get("apiKey", "")).strip()
    except Exception:
        return ""


def _write_key_json(path: Path, new_key: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("main", {})["apiKey"] = new_key
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_key_sqlite(path: Path) -> str:
    if not path.is_file():
        return ""
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT AccessToken FROM ApiKeys ORDER BY Id DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        return str(row[0]).strip() if row and row[0] else ""
    except Exception:
        return ""


_KEY_READERS = {"xml": _read_key_xml, "ini": _read_key_ini, "yaml": _read_key_yaml, "json": _read_key_json, "sqlite": _read_key_sqlite}
_KEY_WRITERS = {"xml": _write_key_xml, "ini": _write_key_ini, "yaml": _write_key_yaml, "json": _write_key_json}
# sqlite keys are rotated via API, not file — handled separately


def _discover_jellyfin_api_key(config_root: str) -> str:
    """Discover Jellyfin API key from the SQLite DB."""
    db_path = Path(config_root) / "jellyfin" / "data" / "jellyfin.db"
    return _read_key_sqlite(db_path)


def jellyfin_hard_reset(username: str, password: str) -> dict[str, Any]:
    """Hard-reset Jellyfin user credentials via direct DB access.

    This handles the Jellyfin 10.11+ race condition where the startup
    wizard auto-completes before the controller can intercept it,
    creating a user with an unknown name and password.

    Steps: stop Jellyfin → clear password in DB → rename user → restart
    → set new password via API.
    """
    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    db_path = Path(config_root) / "jellyfin" / "data" / "jellyfin.db"
    if not db_path.is_file():
        return {"status": "error", "error": "Jellyfin database not found. Start Jellyfin first."}

    jf = SERVICE_MAP.get("jellyfin")
    if not jf:
        return {"status": "error", "error": "Jellyfin not in service registry"}

    import sqlite3

    # 1. Clear password and set username in DB
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("UPDATE Users SET Password='', Username=?, MustUpdatePassword=0", (username,))
        affected = cur.rowcount
        conn.commit()
        conn.close()
    except Exception as exc:
        return {"status": "error", "error": f"DB update failed: {exc}"}

    if affected == 0:
        return {"status": "error", "error": "No users found in Jellyfin DB"}

    # 2. Restart Jellyfin to pick up DB changes
    restart_msg = ""
    try:
        import docker as docker_lib
        client = docker_lib.from_env()
        jf_container = client.containers.get("jellyfin")
        jf_container.restart(timeout=30)
        import time
        for _ in range(15):
            time.sleep(2)
            try:
                req = urllib.request.Request(f"http://{jf.host}:{jf.port}/System/Info/Public")
                urllib.request.urlopen(req, timeout=5)
                restart_msg = "Jellyfin restarted."
                break
            except Exception:
                continue
        else:
            restart_msg = "Jellyfin restarting (health check pending)."
    except Exception:
        restart_msg = "Restart Jellyfin manually."

    # 3. Set the new password via API (now with empty current password)
    pw_set = False
    try:
        # Auth with empty password
        auth_data = json.dumps({"Username": username, "Pw": ""}).encode()
        auth_req = urllib.request.Request(
            f"http://{jf.host}:{jf.port}/Users/AuthenticateByName",
            data=auth_data, method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Emby-Authorization": 'MediaBrowser Client="controller", Device="controller", DeviceId="controller", Version="1.0"',
            },
        )
        with urllib.request.urlopen(auth_req, timeout=10) as resp:
            auth_result = json.loads(resp.read())
        token = auth_result.get("AccessToken", "")
        user_id = auth_result.get("User", {}).get("Id", "")

        if token and user_id:
            pw_data = json.dumps({"CurrentPw": "", "NewPw": password}).encode()
            pw_req = urllib.request.Request(
                f"http://{jf.host}:{jf.port}/Users/{user_id}/Password",
                data=pw_data, method="POST",
                headers={"X-Emby-Token": token, "Content-Type": "application/json"},
            )
            urllib.request.urlopen(pw_req, timeout=10)
            pw_set = True
            os.environ["JELLYFIN_USER_ID"] = user_id
    except Exception as exc:
        return {"status": "partial", "error": f"Password set failed: {exc}", "note": restart_msg}

    if pw_set:
        return {"status": "ok", "user": username, "note": restart_msg}
    return {"status": "partial", "error": "Could not set password after DB reset", "note": restart_msg}


def _discover_jellyfin_admin_user_id(base_url: str, api_key: str, preferred_name: str = "admin") -> str:
    """Find the admin user ID in Jellyfin."""
    try:
        req = urllib.request.Request(
            f"{base_url}/Users?api_key={api_key}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            users = json.loads(resp.read())
        if not users:
            return ""
        # Prefer exact name match, then first admin
        for u in users:
            if str(u.get("Name", "")).strip().lower() == preferred_name.lower():
                return str(u.get("Id", ""))
        for u in users:
            if u.get("Policy", {}).get("IsAdministrator"):
                return str(u.get("Id", ""))
        return str(users[0].get("Id", ""))
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# API key rotation — registry-driven
# ---------------------------------------------------------------------------

def rotate_keys() -> dict[str, Any]:
    """Regenerate API keys for all services that have them."""
    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    rotated: dict[str, str] = {}
    errors: list[str] = []
    file_based_services: list[str] = []

    for svc in get_services_with_api_keys():
        if not svc.api_key_config or not svc.api_key_format:
            continue

        # Jellyfin: rotate via API, not file
        if svc.api_key_format == "sqlite":
            try:
                old_key = _read_key_sqlite(Path(config_root) / svc.api_key_config)
                if old_key:
                    req = urllib.request.Request(
                        f"http://{svc.host}:{svc.port}/Auth/Keys?app=media-stack-controller",
                        method="POST", headers={"X-Emby-Token": old_key},
                    )
                    urllib.request.urlopen(req, timeout=5)
                    new_key = _read_key_sqlite(Path(config_root) / svc.api_key_config)
                    if new_key and new_key != old_key:
                        os.environ[svc.api_key_env] = new_key
                        rotated[svc.api_key_env] = new_key
            except Exception as exc:
                errors.append(f"{svc.id}: {exc}")
            continue

        # File-based rotation
        cfg_path = Path(config_root) / svc.api_key_config
        if not cfg_path.is_file():
            continue

        writer = _KEY_WRITERS.get(svc.api_key_format)
        if not writer:
            continue

        try:
            new_key = uuid.uuid4().hex
            if svc.api_key_format == "json":
                new_key = base64.b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes).decode("utf-8")
            writer(cfg_path, new_key)
            os.environ[svc.api_key_env] = new_key
            rotated[svc.api_key_env] = new_key
            file_based_services.append(svc.id)
        except Exception as exc:
            errors.append(f"{svc.id}: {exc}")

    persist_keys_to_secret(rotated)

    # Auto-restart file-based services
    restarted = []
    for svc_id in file_based_services:
        try:
            restart_service(svc_id)
            restarted.append(svc_id)
        except Exception:
            pass

    return {"status": "rotated", "keys": list(rotated.keys()), "errors": errors, "restarted": restarted}


# ---------------------------------------------------------------------------
# Password reset — registry-driven
# ---------------------------------------------------------------------------

def reset_password(new_password: str) -> dict[str, Any]:
    """Reset admin password across all services that support it."""
    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    old_password = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
    username = os.environ.get("STACK_ADMIN_USERNAME", "admin")
    updated: list[str] = []
    errors: list[str] = []

    # 1. qBittorrent — special case (form-based auth, not in registry pattern)
    qbit = SERVICE_MAP.get("qbittorrent")
    if qbit:
        try:
            cj = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            login_data = f"username={username}&password={old_password}".encode()
            req = urllib.request.Request(f"http://{qbit.host}:{qbit.port}/api/v2/auth/login", data=login_data)
            opener.open(req, timeout=5)
            prefs = json.dumps({"web_ui_password": new_password})
            req2 = urllib.request.Request(
                f"http://{qbit.host}:{qbit.port}/api/v2/app/setPreferences",
                data=("json=" + urllib.parse.quote(prefs)).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            opener.open(req2, timeout=5)
            updated.append("qbittorrent")
        except Exception as exc:
            errors.append(f"qbittorrent: {exc}")

    # 2. Jellyfin — special case (user password API)
    jf = SERVICE_MAP.get("jellyfin")
    if jf:
        try:
            jf_key = os.environ.get("JELLYFIN_API_KEY", "")
            jf_uid = os.environ.get("JELLYFIN_USER_ID", "")
            jf_base = f"http://{jf.host}:{jf.port}"

            # Auto-discover API key and user ID if not in env
            if not jf_key:
                jf_key = _discover_jellyfin_api_key(config_root)
                if jf_key:
                    os.environ["JELLYFIN_API_KEY"] = jf_key
            if jf_key and not jf_uid:
                jf_uid = _discover_jellyfin_admin_user_id(jf_base, jf_key, username)
                if jf_uid:
                    os.environ["JELLYFIN_USER_ID"] = jf_uid

            if jf_key and jf_uid:
                # Try with current password first, then empty password
                pw_set = False
                for current_pw in [old_password, ""]:
                    try:
                        payload = json.dumps({"CurrentPw": current_pw, "NewPw": new_password}).encode()
                        req = urllib.request.Request(
                            f"{jf_base}/Users/{jf_uid}/Password",
                            data=payload, method="POST",
                            headers={"X-Emby-Token": jf_key, "Content-Type": "application/json"},
                        )
                        urllib.request.urlopen(req, timeout=10)
                        pw_set = True
                        break
                    except Exception:
                        continue
                if pw_set:
                    updated.append("jellyfin")
                else:
                    # Hard reset: use ResetPassword endpoint (Jellyfin 10.9+)
                    try:
                        req = urllib.request.Request(
                            f"{jf_base}/Users/{jf_uid}/Password",
                            data=json.dumps({"ResetPassword": True}).encode(),
                            method="POST",
                            headers={"X-Emby-Token": jf_key, "Content-Type": "application/json"},
                        )
                        urllib.request.urlopen(req, timeout=10)
                        # Now set the new password with empty current
                        payload = json.dumps({"CurrentPw": "", "NewPw": new_password}).encode()
                        req = urllib.request.Request(
                            f"{jf_base}/Users/{jf_uid}/Password",
                            data=payload, method="POST",
                            headers={"X-Emby-Token": jf_key, "Content-Type": "application/json"},
                        )
                        urllib.request.urlopen(req, timeout=10)
                        updated.append("jellyfin")
                    except Exception as exc2:
                        errors.append(f"jellyfin: hard reset failed: {exc2}")
            else:
                errors.append("jellyfin: no API key or user ID discoverable")
        except Exception as exc:
            errors.append(f"jellyfin: {exc}")

    # 3. Arr apps — registry-driven via password_api_path
    for svc in get_services_with_password_api():
        try:
            api_key = os.environ.get(svc.api_key_env, "") or _read_key(svc, config_root)
            if not api_key:
                errors.append(f"{svc.id}: no API key available")
                continue
            req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}{svc.password_api_path}",
                headers={"X-Api-Key": api_key, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                cfg = json.loads(resp.read())
            cfg["username"] = username
            cfg["password"] = new_password
            cfg["passwordConfirmation"] = new_password
            put_req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}{svc.password_api_path}",
                data=json.dumps(cfg).encode(), method="PUT",
                headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            )
            urllib.request.urlopen(put_req, timeout=5)
            updated.append(svc.id)
        except Exception as exc:
            errors.append(f"{svc.id}: {exc}")

    # 4. Config-file-based password services — registry-driven
    for svc in get_services_with_password_config():
        if svc.id in updated:
            continue  # Already handled via API
        cfg_path = Path(config_root) / svc.password_config
        if not cfg_path.is_file():
            continue
        try:
            if svc.password_config.endswith(".yaml"):
                import yaml
                with open(cfg_path) as f:
                    data = yaml.safe_load(f) or {}
                data.setdefault("auth", {})["username"] = username
                data["auth"]["password"] = new_password
                data["auth"]["type"] = "form"
                with open(cfg_path, "w") as f:
                    yaml.dump(data, f, default_flow_style=False)
            elif svc.password_config.endswith(".ini"):
                content = cfg_path.read_text(encoding="utf-8")
                if "http_username" in content:
                    content = re.sub(r"^http_username\s*=\s*.*$", f"http_username = {username}", content, count=1, flags=re.MULTILINE)
                    content = re.sub(r"^http_password\s*=\s*.*$", f"http_password = {new_password}", content, count=1, flags=re.MULTILINE)
                else:
                    content = re.sub(r"^username\s*=\s*.*$", f"username = {username}", content, count=1, flags=re.MULTILINE)
                    content = re.sub(r"^password\s*=\s*.*$", f"password = {new_password}", content, count=1, flags=re.MULTILINE)
                cfg_path.write_text(content, encoding="utf-8")
            updated.append(svc.id)
        except Exception as exc:
            errors.append(f"{svc.id}: {exc}")

    # 5. Update env + secret
    os.environ["STACK_ADMIN_PASSWORD"] = new_password
    persist_keys_to_secret({"STACK_ADMIN_PASSWORD": new_password, "STACK_ADMIN_USERNAME": username})

    # 6. Auto-restart file-based services
    restarted = []
    for svc in get_services_with_password_config():
        if svc.id in updated:
            try:
                restart_service(svc.id)
                restarted.append(svc.id)
            except Exception:
                pass

    return {"status": "updated", "services": updated, "errors": errors, "restarted": restarted}


def _read_key(svc: Any, config_root: str) -> str:
    """Read API key for a service using its registry format."""
    reader = _KEY_READERS.get(svc.api_key_format)
    if reader and svc.api_key_config:
        return reader(Path(config_root) / svc.api_key_config)
    return ""


# ---------------------------------------------------------------------------
# Service restart
# ---------------------------------------------------------------------------

def restart_service(service_name: str) -> dict[str, Any]:
    """Restart a single service container or pod."""
    namespace = os.environ.get("K8S_NAMESPACE", "")
    try:
        if namespace:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            v1 = k8s_client.CoreV1Api()
            pods = v1.list_namespaced_pod(namespace, label_selector=f"app={service_name}")
            for pod in pods.items:
                v1.delete_namespaced_pod(name=pod.metadata.name, namespace=namespace)
            return {"status": "restarted", "method": "k8s"}
        else:
            import docker
            client = docker.from_env()
            container = client.containers.get(service_name)
            container.restart(timeout=15)
            return {"status": "restarted", "method": "docker"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:80]}


def batch_restart(service_names: list[str]) -> dict[str, Any]:
    """Restart multiple services."""
    from .health import SERVICE_PROBES
    results: dict[str, Any] = {}
    for name in service_names:
        if name in SERVICE_PROBES:
            results[name] = restart_service(name)
        else:
            results[name] = {"status": "error", "error": f"unknown service '{name}'"}
    ok = sum(1 for v in results.values() if v.get("status") == "restarted")
    return {"results": results, "restarted": ok, "total": len(service_names)}


# ---------------------------------------------------------------------------
# K8s secret persistence
# ---------------------------------------------------------------------------

def persist_keys_to_secret(data: dict[str, str]) -> None:
    """Persist key-value pairs to K8s secret if available."""
    namespace = os.environ.get("K8S_NAMESPACE", "")
    if not namespace or not data:
        return
    try:
        from kubernetes import client as k8s_client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        v1 = k8s_client.CoreV1Api()
        secret_data = {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
        try:
            existing = v1.read_namespaced_secret("media-stack-secrets", namespace)
            if existing.data:
                existing.data.update(secret_data)
            else:
                existing.data = secret_data
            v1.patch_namespaced_secret("media-stack-secrets", namespace, existing)
        except Exception:
            pass
    except Exception:
        pass
