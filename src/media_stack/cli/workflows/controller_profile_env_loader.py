"""ControllerProfileEnvLoader — project bootstrap profile YAML into env vars.

ADR-0015 Phase 7k. Pre-Phase-7k this class lived in
``cli/commands/controller_profile.py``. The class is pure
workflow material (read a YAML file → set env vars the runtime
factory consumes), not HTTP-server glue. Phase 7k moves it to
workflows/; the commands-tier file survives as a re-export shim.
"""

from __future__ import annotations

import os
import pathlib

import yaml


_PROFILE_ENV_DEFAULTS = {
    "APP_PATH_PREFIX": "/app",
    "ROUTE_STRATEGY": "hybrid",
    "MEDIA_STACK_ENV": "prod",
}


class ControllerProfileEnvLoader:
    """Project the ``bootstrap`` / ``routing`` / ``metadata`` profile
    sections into the env vars the runtime factory expects.

    Existing env values win over profile defaults (the loader only
    sets a var if ``os.environ`` doesn't already define it).
    """

    def _apply_profile_env(self, profile_file: str | None) -> None:
        """Read the bootstrap profile YAML and set env vars."""
        if not profile_file:
            return
        path = pathlib.Path(profile_file)
        if not path.is_file():
            return
        profile = self._load_profile_yaml(path)
        if profile is None:
            return

        env_map = self._build_env_map(profile)
        for key, value in env_map.items():
            if not os.environ.get(key):
                os.environ[key] = value

    def _load_profile_yaml(self, path: pathlib.Path) -> dict | None:
        """Parse a YAML profile file. Returns ``None`` if unparseable."""
        try:
            with open(path) as f:
                loaded = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            return None
        return loaded or {}

    def _build_env_map(self, profile: dict) -> dict[str, str]:
        bootstrap_cfg = profile.get("bootstrap") or {}
        routing_cfg = profile.get("routing") or {}
        metadata = profile.get("metadata") or {}

        return {
            "FULLY_PRECONFIGURED": (
                "1" if bootstrap_cfg.get("apply_initial_preferences") else "0"
            ),
            "PRECONFIGURE_API_KEYS": (
                "1" if bootstrap_cfg.get("preconfigure_api_keys") else "0"
            ),
            "APPLY_INITIAL_PREFERENCES": (
                "1" if bootstrap_cfg.get("apply_initial_preferences") else "0"
            ),
            "AUTO_DOWNLOAD_CONTENT": (
                "1" if bootstrap_cfg.get("auto_download_content") else "0"
            ),
            "MEDIA_STACK_ENV": str(
                metadata.get("purpose", _PROFILE_ENV_DEFAULTS["MEDIA_STACK_ENV"])
            ),
            "APP_GATEWAY_HOST": str(routing_cfg.get("gateway_host", "")),
            "APP_GATEWAY_PORT": str(routing_cfg.get("gateway_port", "")),
            "APP_PATH_PREFIX": str(
                routing_cfg.get("app_path_prefix", _PROFILE_ENV_DEFAULTS["APP_PATH_PREFIX"])
            ),
            "ROUTE_STRATEGY": str(
                routing_cfg.get("strategy", _PROFILE_ENV_DEFAULTS["ROUTE_STRATEGY"])
            ),
        }


__all__ = ["ControllerProfileEnvLoader"]
