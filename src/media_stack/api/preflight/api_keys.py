"""Discover API keys from app config files on the shared config mount.

Uses the service registry and shared key readers — no hardcoded app
names or paths. To add a new service, update registry.py only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from media_stack.api.services.admin import _KEY_READERS
from media_stack.api.services.registry import SERVICES
import logging


class ApiKeyPreflightService:
    """Wraps API key discovery preflight logic."""

    def _read_bazarr_api_key(self, config_path: Path) -> str:
        """Extract API key from Bazarr's YAML config (auth.apikey).

        Kept as a named export for backward compatibility with code that
        imports this function directly.
        """
        return _KEY_READERS.get("yaml", lambda p: "")(config_path)

    def run_preflight(
        self,
        *,
        config_root: str = "/srv-config",
        log: Any = None,
        **kwargs: Any,
    ) -> dict[str, str]:
        """Discover API keys from app config files.

        Runs auto-discovery preflight first to detect the real CONFIG_ROOT
        and any API keys injected into container environments, then falls
        back to the standard file-based key readers from the registry.
        """

        def info(msg: str) -> None:
            if log:
                log(msg)

        # --- Auto-discovery preflight (runs before file-based discovery) ---
        discovered: dict[str, str] = {}
        try:
            from media_stack.api.preflight.config_root_discovery import discover_config_root

            discovery = discover_config_root(current_root=config_root, log=log)

            # If discovery found a different config root, switch to it
            if discovery.config_root and discovery.config_root != config_root:
                info(
                    f"[WARN] CONFIG_ROOT mismatch: configured={config_root}, "
                    f"discovered={discovery.config_root} (via {discovery.source}). "
                    f"Using discovered path."
                )
                config_root = discovery.config_root
                import os as _os
                _os.environ["CONFIG_ROOT"] = config_root

            # Merge any keys found from container environments
            if discovery.keys:
                discovered.update(discovery.keys)
                info(
                    f"Auto-discovery preflight: {len(discovery.keys)} key(s) "
                    f"from container inspection"
                )
        except Exception as exc:
            info(f"[WARN] Auto-discovery preflight failed (non-fatal): {exc}")

        root = Path(config_root)

        for svc in SERVICES:
            if not svc.api_key_env or not svc.api_key_config or not svc.api_key_format:
                continue
            # Skip if auto-discovery already found this key
            if svc.api_key_env in discovered:
                continue

            config_path = root / svc.api_key_config
            reader = _KEY_READERS.get(svc.api_key_format)
            if not reader:
                continue

            value = reader(config_path)
            if value:
                discovered[svc.api_key_env] = value
                info(f"API key discovered: {svc.api_key_env} from {svc.api_key_config}")

        # Jellyfin user ID — special case (not an API key, but needed for password reset)
        jf_db = root / "jellyfin" / "data" / "jellyfin.db"
        if jf_db.exists() and "JELLYFIN_API_KEY" in discovered:
            try:
                import sqlite3
                conn = sqlite3.connect(f"file:{jf_db}?mode=ro", uri=True)
                cur = conn.cursor()
                cur.execute("SELECT Id FROM Users WHERE IsAdministrator=1 ORDER BY Id LIMIT 1")
                row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    discovered["JELLYFIN_USER_ID"] = str(row[0]).strip()
                    info(f"API key discovered: JELLYFIN_USER_ID from jellyfin/data/jellyfin.db")
            except Exception as exc:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                pass

        info(f"API key discovery: {len(discovered)} keys found")
        return discovered


_instance = ApiKeyPreflightService()
_read_bazarr_api_key = _instance._read_bazarr_api_key
run_preflight = _instance.run_preflight
