"""qBittorrent-specific admin operations: password reset with default password fallback.

Moved from api/services/admin.py to keep service-specific logic in the app layer.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
import logging


class QbittorrentAdminOps:

    def reset_password(self, 
        svc: Any, username: str, old_password: str, new_password: str, config_root: str,
    ) -> tuple[bool, str]:
        """Reset qBittorrent password, trying multiple known defaults.

        Returns (success, error_message).
        """
        passwords_to_try = [old_password, "adminadmin", ""]

        # Extract temp password from container logs (linuxserver images)
        try:
            import docker
            client = docker.from_env()
            container = client.containers.get(svc.id)
            logs = container.logs(tail=50).decode("utf-8", errors="replace")
            match = re.search(r"temporary password[^:]*:\s*(\S+)", logs, re.IGNORECASE)
            if match:
                passwords_to_try.insert(1, match.group(1))
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass

        for try_pw in passwords_to_try:
            try:
                cj = http.cookiejar.CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
                login_data = f"username={username}&password={try_pw}".encode()
                req = urllib.request.Request(
                    f"http://{svc.host}:{svc.port}/api/v2/auth/login", data=login_data,
                )
                try:
                    resp = opener.open(req, timeout=5)
                except urllib.error.HTTPError as http_err:
                    if http_err.code == 403:
                        import time
                        time.sleep(2)
                        resp = opener.open(req, timeout=5)
                    else:
                        raise
                body = resp.read().decode("utf-8", errors="replace")
                if "Fails" in body:
                    continue
                prefs = json.dumps({"web_ui_password": new_password})
                req2 = urllib.request.Request(
                    f"http://{svc.host}:{svc.port}/api/v2/app/setPreferences",
                    data=("json=" + urllib.parse.quote(prefs)).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                opener.open(req2, timeout=5)
                return True, ""
            except Exception as exc:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                continue

        return False, "login failed with all known passwords"


_instance = QbittorrentAdminOps()
reset_password = _instance.reset_password
