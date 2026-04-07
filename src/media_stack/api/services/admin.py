"""Admin services: API key rotation, password reset, service restart."""

from __future__ import annotations

import json
import os
import re
import uuid
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path
from typing import Any


def rotate_keys() -> dict[str, Any]:
    """Regenerate API keys for all arr apps and update env/secrets."""
    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    rotated: dict[str, str] = {}
    errors: list[str] = []

    # Arr apps: regenerate ApiKey in config.xml
    for app in ("sonarr", "radarr", "lidarr", "readarr", "prowlarr"):
        cfg_path = Path(config_root) / app / "config.xml"
        if not cfg_path.is_file():
            continue
        try:
            content = cfg_path.read_text(encoding="utf-8")
            new_key = uuid.uuid4().hex
            content = re.sub(r"<ApiKey>[^<]*</ApiKey>", f"<ApiKey>{new_key}</ApiKey>", content)
            cfg_path.write_text(content, encoding="utf-8")
            env_key = f"{app.upper()}_API_KEY"
            os.environ[env_key] = new_key
            rotated[env_key] = new_key
        except Exception as exc:
            errors.append(f"{app}: {exc}")

    # Bazarr: regenerate apikey in config/config.yaml
    bazarr_cfg = Path(config_root) / "bazarr" / "config" / "config.yaml"
    if bazarr_cfg.is_file():
        try:
            import yaml
            with open(bazarr_cfg) as f:
                bcfg = yaml.safe_load(f) or {}
            new_key = uuid.uuid4().hex
            bcfg.setdefault("auth", {})["apikey"] = new_key
            with open(bazarr_cfg, "w") as f:
                yaml.dump(bcfg, f, default_flow_style=False)
            os.environ["BAZARR_API_KEY"] = new_key
            rotated["BAZARR_API_KEY"] = new_key
        except Exception as exc:
            errors.append(f"bazarr: {exc}")

    # SABnzbd: regenerate api_key in sabnzbd.ini
    sab_ini = Path(config_root) / "sabnzbd" / "sabnzbd.ini"
    if sab_ini.is_file():
        try:
            content = sab_ini.read_text(encoding="utf-8")
            new_key = uuid.uuid4().hex
            content = re.sub(r"^api_key\s*=\s*.*$", f"api_key = {new_key}", content, flags=re.MULTILINE)
            sab_ini.write_text(content, encoding="utf-8")
            os.environ["SABNZBD_API_KEY"] = new_key
            rotated["SABNZBD_API_KEY"] = new_key
        except Exception as exc:
            errors.append(f"sabnzbd: {exc}")

    # Tautulli: regenerate api_key in config.ini
    tautulli_ini = Path(config_root) / "tautulli" / "config.ini"
    if tautulli_ini.is_file():
        try:
            content = tautulli_ini.read_text(encoding="utf-8")
            new_key = uuid.uuid4().hex
            content = re.sub(r"^api_key\s*=\s*.*$", f"api_key = {new_key}", content, count=1, flags=re.MULTILINE)
            tautulli_ini.write_text(content, encoding="utf-8")
            os.environ["TAUTULLI_API_KEY"] = new_key
            rotated["TAUTULLI_API_KEY"] = new_key
        except Exception as exc:
            errors.append(f"tautulli: {exc}")

    # Jellyfin: create new API key via Jellyfin API, delete old one
    try:
        jf_key = os.environ.get("JELLYFIN_API_KEY", "")
        if not jf_key:
            import sqlite3
            jf_db = Path(config_root) / "jellyfin" / "data" / "jellyfin.db"
            if jf_db.exists():
                conn = sqlite3.connect(f"file:{jf_db}?mode=ro", uri=True)
                cur = conn.cursor()
                cur.execute("SELECT AccessToken FROM ApiKeys ORDER BY Id DESC LIMIT 1")
                row = cur.fetchone()
                conn.close()
                if row:
                    jf_key = row[0]
        if jf_key:
            import urllib.request as _ur
            # Create new key
            req = _ur.Request(
                "http://jellyfin:8096/Auth/Keys?app=media-stack-controller",
                method="POST", headers={"X-Emby-Token": jf_key},
            )
            _ur.urlopen(req, timeout=5)
            # Read new key from DB
            jf_db = Path(config_root) / "jellyfin" / "data" / "jellyfin.db"
            import sqlite3
            conn = sqlite3.connect(f"file:{jf_db}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT AccessToken FROM ApiKeys WHERE Name='media-stack-controller' ORDER BY Id DESC LIMIT 1")
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                new_key = row[0].strip()
                os.environ["JELLYFIN_API_KEY"] = new_key
                rotated["JELLYFIN_API_KEY"] = new_key
    except Exception as exc:
        errors.append(f"jellyfin: {exc}")

    # Jellyseerr: regenerate apiKey in settings.json
    js_settings = Path(config_root) / "jellyseerr" / "settings.json"
    if js_settings.is_file():
        try:
            data = json.loads(js_settings.read_text(encoding="utf-8"))
            import base64
            new_key = base64.b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes).decode("utf-8")
            data.setdefault("main", {})["apiKey"] = new_key
            js_settings.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.environ["JELLYSEERR_API_KEY"] = new_key
            rotated["JELLYSEERR_API_KEY"] = new_key
        except Exception as exc:
            errors.append(f"jellyseerr: {exc}")

    persist_keys_to_secret(rotated)
    restart_needed = [k.replace("_API_KEY", "").lower() for k in rotated
                      if k in ("TAUTULLI_API_KEY", "JELLYSEERR_API_KEY")]
    return {"status": "rotated", "keys": list(rotated.keys()), "errors": errors,
            "restart_needed": restart_needed}


def reset_password(new_password: str) -> dict[str, Any]:
    """Reset admin password across all services."""
    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    old_password = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
    username = os.environ.get("STACK_ADMIN_USERNAME", "admin")
    updated: list[str] = []
    errors: list[str] = []

    # 1. qBittorrent
    try:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        login_data = f"username={username}&password={old_password}".encode()
        req = urllib.request.Request("http://qbittorrent:8080/api/v2/auth/login", data=login_data)
        opener.open(req, timeout=5)
        prefs = json.dumps({"web_ui_password": new_password})
        req2 = urllib.request.Request(
            "http://qbittorrent:8080/api/v2/app/setPreferences",
            data=("json=" + urllib.parse.quote(prefs)).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        opener.open(req2, timeout=5)
        updated.append("qbittorrent")
    except Exception as exc:
        errors.append(f"qbittorrent: {exc}")

    # 2. Jellyfin
    try:
        jf_key = os.environ.get("JELLYFIN_API_KEY", "")
        jf_uid = os.environ.get("JELLYFIN_USER_ID", "")
        if jf_key and jf_uid:
            payload = json.dumps({"CurrentPw": old_password, "NewPw": new_password}).encode()
            req = urllib.request.Request(
                f"http://jellyfin:8096/Users/{jf_uid}/Password",
                data=payload, method="POST",
                headers={"X-Emby-Token": jf_key, "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            updated.append("jellyfin")
        else:
            errors.append("jellyfin: JELLYFIN_API_KEY or JELLYFIN_USER_ID not set")
    except Exception as exc:
        errors.append(f"jellyfin: {exc}")

    # 3. Arr apps
    arr_apps = [
        ("sonarr", 8989, "/api/v3/config/host", "SONARR_API_KEY"),
        ("radarr", 7878, "/api/v3/config/host", "RADARR_API_KEY"),
        ("lidarr", 8686, "/api/v1/config/host", "LIDARR_API_KEY"),
        ("readarr", 8787, "/api/v1/config/host", "READARR_API_KEY"),
        ("prowlarr", 9696, "/api/v1/config/host", "PROWLARR_API_KEY"),
    ]
    for app, port, api_path, key_env in arr_apps:
        try:
            api_key = os.environ.get(key_env, "") or _read_xml_key(Path(config_root) / app / "config.xml")
            if not api_key:
                errors.append(f"{app}: no API key available")
                continue
            req = urllib.request.Request(
                f"http://{app}:{port}{api_path}",
                headers={"X-Api-Key": api_key, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                cfg = json.loads(resp.read())
            cfg["username"] = username
            cfg["password"] = new_password
            cfg["passwordConfirmation"] = new_password
            put_req = urllib.request.Request(
                f"http://{app}:{port}{api_path}",
                data=json.dumps(cfg).encode(), method="PUT",
                headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            )
            urllib.request.urlopen(put_req, timeout=5)
            updated.append(app)
        except Exception as exc:
            errors.append(f"{app}: {exc}")

    # 4. Bazarr
    try:
        bazarr_cfg = Path(config_root) / "bazarr" / "config" / "config.yaml"
        if bazarr_cfg.is_file():
            import yaml
            with open(bazarr_cfg) as f:
                bcfg = yaml.safe_load(f) or {}
            bcfg.setdefault("auth", {})["username"] = username
            bcfg["auth"]["password"] = new_password
            bcfg["auth"]["type"] = "form"
            with open(bazarr_cfg, "w") as f:
                yaml.dump(bcfg, f, default_flow_style=False)
            updated.append("bazarr")
    except Exception as exc:
        errors.append(f"bazarr: {exc}")

    # 5. SABnzbd — set username/password in INI
    try:
        sab_ini = Path(config_root) / "sabnzbd" / "sabnzbd.ini"
        if sab_ini.is_file():
            content = sab_ini.read_text(encoding="utf-8")
            content = re.sub(r"^username\s*=\s*.*$", f"username = {username}", content, count=1, flags=re.MULTILINE)
            content = re.sub(r"^password\s*=\s*.*$", f"password = {new_password}", content, count=1, flags=re.MULTILINE)
            sab_ini.write_text(content, encoding="utf-8")
            updated.append("sabnzbd")
    except Exception as exc:
        errors.append(f"sabnzbd: {exc}")

    # 6. Tautulli — set http_username/http_password in config.ini
    try:
        tautulli_ini = Path(config_root) / "tautulli" / "config.ini"
        if tautulli_ini.is_file():
            content = tautulli_ini.read_text(encoding="utf-8")
            if "http_username" in content:
                content = re.sub(r"^http_username\s*=\s*.*$", f"http_username = {username}", content, count=1, flags=re.MULTILINE)
                content = re.sub(r"^http_password\s*=\s*.*$", f"http_password = {new_password}", content, count=1, flags=re.MULTILINE)
            else:
                # Append under [General] section
                content = content.replace("[General]", f"[General]\nhttp_username = {username}\nhttp_password = {new_password}", 1)
            tautulli_ini.write_text(content, encoding="utf-8")
            updated.append("tautulli")
    except Exception as exc:
        errors.append(f"tautulli: {exc}")

    # 7. Update env var
    os.environ["STACK_ADMIN_PASSWORD"] = new_password

    # 8. Persist to K8s secret
    persist_keys_to_secret({
        "STACK_ADMIN_PASSWORD": new_password,
        "STACK_ADMIN_USERNAME": username,
    })

    restart_needed = [s for s in updated if s in ("bazarr", "sabnzbd", "tautulli")]
    return {"status": "updated", "services": updated, "errors": errors, "restart_needed": restart_needed}


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


def persist_keys_to_secret(data: dict[str, str]) -> None:
    """Persist key-value pairs to K8s secret if available."""
    namespace = os.environ.get("K8S_NAMESPACE", "")
    if not namespace or not data:
        return
    try:
        from kubernetes import client as k8s_client, config as k8s_config
        import base64
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


def _read_xml_key(path: Path) -> str:
    """Read ApiKey from an arr app config.xml."""
    try:
        content = path.read_text(encoding="utf-8")
        m = re.search(r"<ApiKey>([^<]+)</ApiKey>", content)
        return m.group(1) if m else ""
    except Exception:
        return ""
