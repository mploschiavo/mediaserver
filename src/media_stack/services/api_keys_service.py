"""API key and config file resolution helpers used during bootstrap."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
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
        env_value = self.read_api_key_from_env(app_name)
        if env_value:
            return env_value

        timing = self._resolve_poll_timing()
        candidate_paths = self._resolve_candidate_config_paths(config_root, app_name)
        timing["timeout_seconds"] = self._maybe_shorten_timeout_for_fast_fail(
            app_name, candidate_paths, timing["timeout_seconds"],
        )

        key_or_error = self._poll_for_api_key(
            app_name=app_name,
            config_root=config_root,
            candidate_paths=candidate_paths,
            timing=timing,
        )
        if key_or_error["key"]:
            return key_or_error["key"]

        http_key = self._try_recover_via_http(app_name)
        if http_key:
            return http_key
        raise RuntimeError(
            f"Unable to read API key for {app_name} after {key_or_error['elapsed']}s "
            f"(last_error={key_or_error['last_error']})."
        )

    def _resolve_poll_timing(self) -> dict[str, int]:
        """Return timeout/heartbeat/interval seconds from env with safe floors.

        Kept separate so callers don't have to reason about the three
        interlocking ``BOOTSTRAP_APIKEY_FILE_*`` env vars every time.
        """
        return {
            "timeout_seconds": max(
                5, self.to_int(os.environ.get("BOOTSTRAP_APIKEY_FILE_TIMEOUT_SECONDS"), 180) or 180,
            ),
            "heartbeat_seconds": max(
                5, self.to_int(os.environ.get("BOOTSTRAP_APIKEY_FILE_HEARTBEAT_SECONDS"), 15) or 15,
            ),
            "interval_seconds": max(
                1, self.to_int(os.environ.get("BOOTSTRAP_APIKEY_FILE_INTERVAL_SECONDS"), 2) or 2,
            ),
        }

    def _resolve_candidate_config_paths(self, config_root: str, app_name: str) -> list[Path]:
        """Resolve the list of config-file paths to probe for this app.

        Registry entry's ``api_key_config`` wins over the legacy
        ``<app>/config.xml`` default.
        """
        rel_path = f"{app_name}/config.xml"
        try:
            from media_stack.core.service_registry.registry import SERVICE_MAP
            svc = SERVICE_MAP.get(app_name)
            if svc and svc.api_key_config:
                rel_path = svc.api_key_config
        except Exception as exc:
            log_swallowed(exc)
        return [root / rel_path for root in self.candidate_config_roots(config_root)]

    def _maybe_shorten_timeout_for_fast_fail(
        self, app_name: str, candidate_paths: list[Path], timeout_seconds: int,
    ) -> int:
        """If no config file exists yet, shrink the wait to the fast-fail floor."""
        if any(p.exists() for p in candidate_paths):
            return timeout_seconds
        fast_timeout = min(timeout_seconds, max(5, self.to_int(
            os.environ.get("BOOTSTRAP_APIKEY_FAST_TIMEOUT_SECONDS"), 15) or 15))
        self.log(
            f"[INFO] {app_name}: config file not found, "
            f"using fast timeout ({fast_timeout}s instead of {timeout_seconds}s)"
        )
        return fast_timeout

    def _poll_for_api_key(
        self,
        *,
        app_name: str,
        config_root: str,
        candidate_paths: list[Path],
        timing: dict[str, int],
    ) -> dict[str, Any]:
        """Poll registry readers + candidate paths until a key shows up or we time out.

        Returns ``{"key": str, "elapsed": int, "last_error": str}``; the
        caller interprets an empty ``key`` as "fall through to HTTP".
        """
        start = time.time()
        next_heartbeat = start
        last_error = ""
        timeout_seconds = timing["timeout_seconds"]
        interval_seconds = timing["interval_seconds"]
        heartbeat_seconds = timing["heartbeat_seconds"]
        while True:
            key, last_error = self._attempt_one_poll_cycle(
                app_name=app_name, config_root=config_root,
                candidate_paths=candidate_paths, last_error=last_error,
                interval_seconds=interval_seconds, start=start,
            )
            if key:
                return {"key": key, "elapsed": int(time.time() - start), "last_error": last_error}
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
        return {"key": "", "elapsed": int(time.time() - start), "last_error": last_error}

    def _attempt_one_poll_cycle(
        self,
        *,
        app_name: str,
        config_root: str,
        candidate_paths: list[Path],
        last_error: str,
        interval_seconds: int,
        start: float,
    ) -> tuple[str, str]:
        """Run one pass of the poll: registry reader → periodic HTTP → scan paths.

        Returns ``(key_or_empty, updated_last_error)``. Extracted so the
        outer loop body is a small scheduler instead of three tiered
        fallback blocks.
        """
        # Try the registry's format-aware reader first
        try:
            from media_stack.core.service_registry.registry import read_api_key_from_file
            key = read_api_key_from_file(app_name, config_root)
            if key:
                return key, last_error
        except Exception as exc:
            log_swallowed(exc)
        # Also try HTTP early (every 10s) — service may be up even
        # when config file isn't visible to the controller.
        elapsed_now = int(time.time() - start)
        if elapsed_now > 5 and elapsed_now % 10 < interval_seconds + 1:
            http_key = self._try_recover_via_http(app_name)
            if http_key:
                return http_key, last_error
        return self._scan_candidate_paths(app_name, candidate_paths, last_error)

    @staticmethod
    def _scan_candidate_paths(
        app_name: str, candidate_paths: list[Path], last_error: str,
    ) -> tuple[str, str]:
        """Probe each config file with its registry-declared reader.

        Returns ``(key_or_empty, last_error)`` so the caller can
        distinguish "found" from "keep polling".
        """
        for cfg_path in candidate_paths:
            if not cfg_path.exists():
                last_error = f"Missing config file(s): {', '.join(str(p) for p in candidate_paths if not p.exists())}"
                continue
            try:
                from media_stack.core.service_registry.registry import KEY_READERS, SERVICE_MAP as _sm
                _svc = _sm.get(app_name)
                _fmt = _svc.api_key_format if _svc and _svc.api_key_format else "xml"
                _reader = KEY_READERS.get(_fmt)
                if _reader:
                    key = _reader(cfg_path)
                    if key:
                        return key, last_error
                last_error = f"API key not found in {cfg_path} (format={_fmt})"
            except Exception as exc:
                last_error = f"{cfg_path}: {exc}"
        return "", last_error

    def _try_recover_via_http(self, app_name: str) -> str:
        """Ask the registry's HTTP reader for a live-service key (best effort).

        On success stores the key in env so subsequent reads short-circuit
        at priority 1.
        """
        try:
            from media_stack.core.service_registry.registry import read_api_key_via_http
            http_key = read_api_key_via_http(app_name)
            if http_key:
                env_name = f"{app_name.upper()}_API_KEY"
                os.environ[env_name] = http_key
                self.log(f"[OK] {app_name}: recovered API key via HTTP")
                return http_key
        except Exception as exc:
            log_swallowed(exc)
        return ""

    def read_json_file(self, path: Any) -> dict[str, Any]:
        file_path = Path(path)
        if not file_path.exists():
            raise RuntimeError(f"Missing file: {file_path}")
        return json.loads(file_path.read_text(encoding="utf-8", errors="replace"))

