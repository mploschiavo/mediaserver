"""Bazarr-specific admin operations: password reset via API.

Bazarr uses a PATCH endpoint for auth settings, not the standard
password_api_path pattern used by Arr apps.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


class BazarrAdminOps:

    def reset_password(
        self, svc: Any, username: str, old_password: str, new_password: str, config_root: str,
    ) -> tuple[bool, str]:
        """Reset Bazarr password via its settings API.

        Returns (success, error_message).
        """
        from media_stack.api.services.key_formats import READERS
        from pathlib import Path

        api_key = os.environ.get(svc.api_key_env, "") if svc.api_key_env else ""
        if not api_key and svc.api_key_config and svc.api_key_format:
            reader = READERS.get(svc.api_key_format)
            if reader:
                api_key = reader(Path(config_root) / svc.api_key_config)
        if not api_key:
            return False, "no API key available"

        try:
            req = urllib.request.Request(
                f"http://{svc.host}:{svc.port}/api/system/settings",
                data=json.dumps({
                    "auth": {"type": "basic", "username": username, "password": new_password},
                }).encode(),
                method="PATCH",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            return True, ""
        except Exception as exc:
            return False, str(exc)[:120]


_instance = BazarrAdminOps()
reset_password = _instance.reset_password


__all__ = [
    "BazarrAdminOps",
    "reset_password",
]
