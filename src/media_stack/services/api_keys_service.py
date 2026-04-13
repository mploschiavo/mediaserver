"""API key and config file resolution helpers used during bootstrap."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import logging

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
        """Discover an API key using the priority chain:

        1. Environment variable (``{APP}_API_KEY``) — instant.
        2. Config file (registry-driven format) — polls until timeout.
        3. HTTP fetch from running service (registry ``api_key_http_path``) — last resort.

        All format-specific logic lives in the service registry module
        (``registry.KEY_READERS``).  To support a new config format, add a
        reader there and declare ``api_key_format`` in the service YAML
        contract — no changes to this file required.
        """
        # --- Priority 1: env var ---
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

        # Build candidate paths using the service registry contract
        rel_path = f"{app_name}/config.xml"  # legacy default
        try:
            from media_stack.api.services.registry import SERVICE_MAP
            svc = SERVICE_MAP.get(app_name)
            if svc and svc.api_key_config:
                rel_path = svc.api_key_config
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass

        candidate_paths = [root / rel_path for root in self.candidate_config_roots(config_root)]
        start = time.time()
        next_heartbeat = start
        last_error = ""

        # Fast-fail: if no config file exists and the directory is empty,
        # the service hasn't started or isn't sharing its config with us.
        # Use a short timeout (15s) instead of blocking for 180s.
        any_file_exists = any(p.exists() for p in candidate_paths)
        if not any_file_exists:
            fast_timeout = min(timeout_seconds, max(5, self.to_int(
                os.environ.get("BOOTSTRAP_APIKEY_FAST_TIMEOUT_SECONDS"), 15) or 15))
            self.log(
                f"[INFO] {app_name}: config file not found, "
                f"using fast timeout ({fast_timeout}s instead of {timeout_seconds}s)"
            )
            timeout_seconds = fast_timeout

        # --- Priority 2: config file (registry-driven reader) ---
        while True:
            # Try the registry's format-aware reader first
            try:
                from media_stack.api.services.registry import read_api_key_from_file
                key = read_api_key_from_file(app_name, config_root)
                if key:
                    return key
            except Exception as exc:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                pass

            # Also try HTTP early (every 10s) — service may be up even
            # when config file isn't visible to the controller.
            elapsed_now = int(time.time() - start)
            if elapsed_now > 5 and elapsed_now % 10 < interval_seconds + 1:
                try:
                    from media_stack.api.services.registry import read_api_key_via_http
                    http_key = read_api_key_via_http(app_name)
                    if http_key:
                        env_name = f"{app_name.upper()}_API_KEY"
                        os.environ[env_name] = http_key
                        self.log(f"[OK] {app_name}: recovered API key via HTTP")
                        return http_key
                except Exception as exc:
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                    pass

            # Also check alt config roots
            for cfg_path in candidate_paths:
                if not cfg_path.exists():
                    last_error = f"Missing config file(s): {', '.join(str(p) for p in candidate_paths if not p.exists())}"
                    continue
                try:
                    from media_stack.api.services.registry import KEY_READERS, SERVICE_MAP as _sm
                    _svc = _sm.get(app_name)
                    _fmt = _svc.api_key_format if _svc and _svc.api_key_format else "xml"
                    _reader = KEY_READERS.get(_fmt)
                    if _reader:
                        key = _reader(cfg_path)
                        if key:
                            return key
                    last_error = f"API key not found in {cfg_path} (format={_fmt})"
                except Exception as exc:
                    last_error = f"{cfg_path}: {exc}"

            now = time.time()
            elapsed = int(now - start)
            if elapsed >= timeout_seconds:
                break

            if now >= next_heartbeat:
                self.log(
                    f"[WAIT] {app_name}: waiting for API key material "
                    f"(paths={', '.join(str(p) for p in candidate_paths)}, "
                    f"elapsed={elapsed}s, timeout={timeout_seconds}s, "
                    f"last_error={last_error})"
                )
                next_heartbeat = now + heartbeat_seconds

            time.sleep(interval_seconds)

        # --- Priority 3: final HTTP fetch attempt ---
        try:
            from media_stack.api.services.registry import read_api_key_via_http
            http_key = read_api_key_via_http(app_name)
            if http_key:
                env_name = f"{app_name.upper()}_API_KEY"
                os.environ[env_name] = http_key
                self.log(f"[OK] {app_name}: recovered API key via HTTP")
                return http_key
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass

        raise RuntimeError(
            f"Unable to read API key for {app_name} after {int(time.time() - start)}s "
            f"(last_error={last_error})."
        )

    def read_json_file(self, path: Any) -> dict[str, Any]:
        file_path = Path(path)
        if not file_path.exists():
            raise RuntimeError(f"Missing file: {file_path}")
        return json.loads(file_path.read_text(encoding="utf-8", errors="replace"))

