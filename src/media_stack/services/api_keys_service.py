"""API key and config file resolution helpers used during bootstrap."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]
ToIntFn = Callable[[Any, Any], Any]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ResolvePathFn = Callable[[str, Any], Path]


@dataclass
class ApiKeysService:
    log: LogFn
    to_int: ToIntFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    resolve_path: ResolvePathFn

    def candidate_config_roots(self, config_root: str) -> list[Path]:
        roots = [Path(str(config_root))]
        alt_root = (os.environ.get("BOOTSTRAP_ALT_CONFIG_ROOT") or "").strip()
        if alt_root:
            alt_path = Path(alt_root)
            if alt_path not in roots:
                roots.append(alt_path)
        return roots

    def read_api_key_from_env(self, app_name: str) -> str:
        app_token = str(app_name or "").strip().upper()
        if not app_token:
            return ""

        candidates = [f"{app_token}_API_KEY"]
        for env_name in candidates:
            value = (os.environ.get(env_name) or "").strip()
            if not value:
                continue
            if value.lower() == "replace-after-first-boot":
                continue
            self.log(f"[OK] {app_name}: using API key from env {env_name}")
            return value
        return ""

    def read_api_key(self, config_root: str, app_name: str) -> str:
        env_value = self.read_api_key_from_env(app_name)
        if env_value:
            return env_value

        timeout_seconds = max(
            5, self.to_int(os.environ.get("BOOTSTRAP_APIKEY_FILE_TIMEOUT_SECONDS"), 180) or 180
        )
        heartbeat_seconds = max(
            5, self.to_int(os.environ.get("BOOTSTRAP_APIKEY_FILE_HEARTBEAT_SECONDS"), 15) or 15
        )
        interval_seconds = max(
            1, self.to_int(os.environ.get("BOOTSTRAP_APIKEY_FILE_INTERVAL_SECONDS"), 2) or 2
        )

        xml_paths = [
            root / app_name / "config.xml" for root in self.candidate_config_roots(config_root)
        ]
        start = time.time()
        next_heartbeat = start
        last_error = ""

        while True:
            missing_paths = []
            for xml_path in xml_paths:
                if xml_path.exists():
                    try:
                        text = xml_path.read_text(encoding="utf-8", errors="replace")
                        match = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
                        if match and match.group(1).strip():
                            return match.group(1).strip()
                        last_error = f"ApiKey not found in {xml_path}"
                    except Exception as exc:
                        last_error = f"{xml_path}: {exc}"
                else:
                    missing_paths.append(str(xml_path))
            if missing_paths and not last_error:
                last_error = f"Missing config file(s): {', '.join(missing_paths)}"

            now = time.time()
            elapsed = int(now - start)
            if elapsed >= timeout_seconds:
                raise RuntimeError(
                    f"Unable to read API key for {app_name} after {elapsed}s "
                    f"(last_error={last_error})."
                )

            if now >= next_heartbeat:
                self.log(
                    f"[WAIT] {app_name}: waiting for API key material "
                    f"(paths={', '.join(str(p) for p in xml_paths)}, "
                    f"elapsed={elapsed}s, timeout={timeout_seconds}s, "
                    f"last_error={last_error})"
                )
                next_heartbeat = now + heartbeat_seconds

            time.sleep(interval_seconds)

    def read_bazarr_api_key(self, config_root: str, timeout_seconds: int = 60) -> str:
        """Read Bazarr API key from its YAML config file."""
        yaml_paths = [
            root / "bazarr" / "config" / "config.yaml"
            for root in self.candidate_config_roots(config_root)
        ]
        start = time.time()
        next_heartbeat = start
        interval = 2

        while True:
            for yaml_path in yaml_paths:
                if not yaml_path.exists():
                    continue
                try:
                    text = yaml_path.read_text(encoding="utf-8", errors="replace")
                    match = re.search(
                        r"^\s*apikey:\s*['\"]?(\S+?)['\"]?\s*$", text, flags=re.MULTILINE
                    )
                    if match and match.group(1).strip():
                        return match.group(1).strip()
                except Exception:
                    pass

            now = time.time()
            elapsed = int(now - start)
            if elapsed >= int(timeout_seconds):
                raise RuntimeError(
                    f"Bazarr API key not found after {elapsed}s "
                    f"(paths={', '.join(str(p) for p in yaml_paths)})"
                )

            if now >= next_heartbeat:
                self.log(
                    f"[WAIT] Bazarr: waiting for api key in "
                    f"{', '.join(str(p) for p in yaml_paths)} "
                    f"(elapsed={elapsed}s, timeout={timeout_seconds}s)"
                )
                next_heartbeat = now + 15

            time.sleep(interval)

    def read_json_file(self, path: Any) -> dict[str, Any]:
        file_path = Path(path)
        if not file_path.exists():
            raise RuntimeError(f"Missing file: {file_path}")
        return json.loads(file_path.read_text(encoding="utf-8", errors="replace"))

    def read_jellyseerr_api_key(self, config_root: str, timeout_seconds: int = 120) -> str:
        settings_paths = [
            root / "jellyseerr" / "settings.json"
            for root in self.candidate_config_roots(config_root)
        ]
        start = time.time()
        next_heartbeat = start
        interval = 2

        while True:
            for settings_path in settings_paths:
                if not settings_path.exists():
                    continue
                try:
                    data = self.read_json_file(settings_path)
                    api_key = ((data.get("main") or {}).get("apiKey") or "").strip()
                    if api_key:
                        return api_key
                except Exception:
                    # File can exist but still be in the middle of being written on first boot.
                    pass

            now = time.time()
            elapsed = int(now - start)
            if elapsed >= int(timeout_seconds):
                raise RuntimeError(
                    "Jellyseerr API key not found after "
                    f"{elapsed}s (paths={', '.join(str(p) for p in settings_paths)})"
                )

            if now >= next_heartbeat:
                self.log(
                    "[WAIT] Jellyseerr: waiting for api key in "
                    f"{', '.join(str(p) for p in settings_paths)} "
                    f"(elapsed={elapsed}s, timeout={timeout_seconds}s)"
                )
                next_heartbeat = now + 15

            time.sleep(interval)

    def read_jellyfin_api_key_from_db(
        self, config_root: str, jellyfin_cfg: dict[str, Any]
    ) -> tuple[str, str]:
        db_rel_path = jellyfin_cfg.get("api_key_db_path", "jellyfin/data/jellyfin.db")
        db_path = self.resolve_path(config_root, db_rel_path)
        if not db_path.exists():
            raise RuntimeError(f"Jellyfin API key db not found: {db_path}")

        preferred_names = self.coerce_list(
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

        by_name = {}
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

    def resolve_jellyfin_api_key(self, jellyfin_cfg: dict[str, Any], config_root: str) -> str:
        api_key_env = jellyfin_cfg.get("api_key_env", "JELLYFIN_API_KEY")
        env_value = (os.environ.get(api_key_env) or "").strip()
        if env_value:
            self.log(f"[OK] Jellyfin: using API key from env {api_key_env}")
            return env_value

        if self.bool_cfg(jellyfin_cfg, "auto_discover_api_key_from_db", True):
            token, source_name = self.read_jellyfin_api_key_from_db(config_root, jellyfin_cfg)
            self.log(
                "[OK] Jellyfin: discovered API key from db " f"(source key name='{source_name}')"
            )
            return token

        return ""
