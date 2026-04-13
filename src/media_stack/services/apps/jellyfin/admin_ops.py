"""Jellyfin-specific admin operations: hard reset, password reset, API key discovery.

Moved from api/services/admin.py to keep service-specific logic in the app layer.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any
import logging


class JellyfinAdminOps:

    def discover_api_key(self, config_root: str) -> str:
        """Discover Jellyfin API key from the SQLite DB."""
        from media_stack.api.services.key_formats import READERS
        db_path = Path(config_root) / "jellyfin" / "data" / "jellyfin.db"
        return READERS["sqlite"](db_path)

    def discover_admin_user_id(self, base_url: str, api_key: str, username: str) -> str:
        """Find the admin user ID by username."""
        try:
            req = urllib.request.Request(
                f"{base_url}/Users", headers={"X-Emby-Token": api_key},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                users = json.loads(resp.read())
            for u in users:
                if u.get("Name", "").lower() == username.lower():
                    return u.get("Id", "")
            # Fallback: first admin user
            for u in users:
                policy = u.get("Policy") or {}
                if policy.get("IsAdministrator"):
                    return u.get("Id", "")
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        return ""

    def reset_password(
        self, svc: Any, username: str, old_password: str, new_password: str, config_root: str,
    ) -> tuple[bool, str]:
        """Reset Jellyfin admin password via API.

        Returns (success, error_message).
        """
        jf_key = os.environ.get("JELLYFIN_API_KEY", "")
        jf_uid = os.environ.get("JELLYFIN_USER_ID", "")
        jf_base = f"http://{svc.host}:{svc.port}"

        if not jf_key:
            jf_key = self.discover_api_key(config_root)
            if jf_key:
                os.environ["JELLYFIN_API_KEY"] = jf_key
        if jf_key and not jf_uid:
            jf_uid = self.discover_admin_user_id(jf_base, jf_key, username)
            if jf_uid:
                os.environ["JELLYFIN_USER_ID"] = jf_uid

        if not jf_key or not jf_uid:
            return False, "no API key or user ID discoverable"

        # Try with current password first, then empty
        for current_pw in [old_password, ""]:
            try:
                payload = json.dumps({"CurrentPw": current_pw, "NewPw": new_password}).encode()
                req = urllib.request.Request(
                    f"{jf_base}/Users/{jf_uid}/Password",
                    data=payload, method="POST",
                    headers={"X-Emby-Token": jf_key, "Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)
                return True, ""
            except Exception as exc:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                continue

        # Hard reset fallback (Jellyfin 10.9+)
        try:
            req = urllib.request.Request(
                f"{jf_base}/Users/{jf_uid}/Password",
                data=json.dumps({"ResetPassword": True}).encode(),
                method="POST",
                headers={"X-Emby-Token": jf_key, "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            payload = json.dumps({"CurrentPw": "", "NewPw": new_password}).encode()
            req = urllib.request.Request(
                f"{jf_base}/Users/{jf_uid}/Password",
                data=payload, method="POST",
                headers={"X-Emby-Token": jf_key, "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            return True, ""
        except Exception as exc:
            return False, f"hard reset failed: {exc}"

    def hard_reset(self, username: str, password: str) -> dict[str, Any]:
        """Hard-reset Jellyfin user credentials via direct DB access."""
        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        db_path = Path(config_root) / "jellyfin" / "data" / "jellyfin.db"
        if not db_path.is_file():
            return {"status": "error", "error": "Media server database not found. Start the service first."}

        from media_stack.api.services.registry import SERVICE_MAP
        svc = next((s for s in SERVICE_MAP.values() if s.category == "media"), None)
        if not svc:
            return {"status": "error", "error": "Media server not in service registry"}

        import sqlite3
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
            return {"status": "error", "error": "No users found in media server DB"}

        # Restart
        restart_msg = ""
        try:
            import docker as docker_lib
            client = docker_lib.from_env()
            container = client.containers.get(svc.id)
            container.restart(timeout=30)
            import time
            for _ in range(15):
                time.sleep(2)
                try:
                    req = urllib.request.Request(f"http://{svc.host}:{svc.port}{svc.health_path}")
                    urllib.request.urlopen(req, timeout=5)
                    restart_msg = "Service restarted."
                    break
                except Exception as exc:
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                    continue
            else:
                restart_msg = "Service restarting (health check pending)."
        except Exception:
            restart_msg = "Restart the service manually."

        # Set password via API
        try:
            auth_data = json.dumps({"Username": username, "Pw": ""}).encode()
            auth_req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}/Users/AuthenticateByName",
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
                    f"http://{svc.host}:{svc.port}/Users/{user_id}/Password",
                    data=pw_data, method="POST",
                    headers={"X-Emby-Token": token, "Content-Type": "application/json"},
                )
                urllib.request.urlopen(pw_req, timeout=10)
                return {"status": "ok", "user": username, "note": restart_msg}
        except Exception as exc:
            return {"status": "partial", "error": f"Password set failed: {exc}", "note": restart_msg}

        return {"status": "partial", "error": "Could not set password after DB reset", "note": restart_msg}


_instance = JellyfinAdminOps()
discover_api_key = _instance.discover_api_key
discover_admin_user_id = _instance.discover_admin_user_id
reset_password = _instance.reset_password
hard_reset = _instance.hard_reset
