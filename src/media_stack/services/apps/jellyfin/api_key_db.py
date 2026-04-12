"""Jellyfin-specific API key discovery from the Jellyfin SQLite database."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ResolvePathFn = Callable[[str, Any], Path]


class JellyfinApiKeyDb:

    def read_jellyfin_api_key_from_db(self, 
        config_root: str,
        jellyfin_cfg: dict[str, Any],
        *,
        coerce_list: CoerceListFn,
        resolve_path: ResolvePathFn,
    ) -> tuple[str, str]:
        """Read a Jellyfin API key directly from the Jellyfin SQLite database.

        Returns ``(token, source_key_name)`` for the best-matching key.
        """
        db_rel_path = jellyfin_cfg.get("api_key_db_path", "jellyfin/data/jellyfin.db")
        db_path = resolve_path(config_root, db_rel_path)
        if not db_path.exists():
            raise RuntimeError(f"Jellyfin API key db not found: {db_path}")

        preferred_names = coerce_list(
            jellyfin_cfg.get("api_key_name_preference", ["Jellyfin", "Jellyseerr"])
        )
        preferred_names = [str(x).strip().lower() for x in preferred_names if str(x).strip()]

        conn = None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT Id, Name, AccessToken FROM ApiKeys ORDER BY Id DESC")
            rows = cur.fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError(f"Jellyfin API key db query failed ({db_path}): {exc}") from exc
        finally:
            if conn is not None:
                conn.close()

        if not rows:
            raise RuntimeError(f"No API keys found in {db_path}")

        by_name: dict[str, str] = {}
        for _, name, token in rows:
            key_name = str(name or "").strip().lower()
            if key_name and token and key_name not in by_name:
                by_name[key_name] = str(token).strip()

        for preferred in preferred_names:
            token = by_name.get(preferred)
            if token:
                return token, preferred

        for _, name, token in rows:
            if token:
                return str(token).strip(), str(name or "unknown")

        raise RuntimeError(f"No usable API token found in {db_path}")

    def resolve_jellyfin_api_key(self, 
        jellyfin_cfg: dict[str, Any],
        config_root: str,
        *,
        log: LogFn,
        bool_cfg: BoolCfgFn,
        coerce_list: CoerceListFn,
        resolve_path: ResolvePathFn,
    ) -> str:
        """Resolve a Jellyfin API key: env var first, then SQLite DB discovery."""
        api_key_env = jellyfin_cfg.get("api_key_env", "JELLYFIN_API_KEY")
        env_value = (os.environ.get(api_key_env) or "").strip()
        if env_value:
            log(f"[OK] Jellyfin: using API key from env {api_key_env}")
            return env_value

        if bool_cfg(jellyfin_cfg, "auto_discover_api_key_from_db", True):
            token, source_name = read_jellyfin_api_key_from_db(
                config_root,
                jellyfin_cfg,
                coerce_list=coerce_list,
                resolve_path=resolve_path,
            )
            log(
                "[OK] Jellyfin: discovered API key from db " f"(source key name='{source_name}')"
            )
            return token

        return ""


_instance = JellyfinApiKeyDb()
read_jellyfin_api_key_from_db = _instance.read_jellyfin_api_key_from_db
resolve_jellyfin_api_key = _instance.resolve_jellyfin_api_key
