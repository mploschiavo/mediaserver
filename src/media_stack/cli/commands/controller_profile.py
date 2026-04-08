"""Profile env setup for the bootstrap controller."""

from __future__ import annotations

import os

import media_stack.services.runtime_platform as runtime_platform


def _apply_profile_env(profile_file: str | None) -> None:
    """Read the bootstrap profile YAML and set env vars that the runtime factory expects."""
    if not profile_file:
        return
    path = __import__("pathlib").Path(profile_file)
    if not path.is_file():
        return
    try:
        import yaml

        with open(path) as f:
            profile = yaml.safe_load(f) or {}
    except Exception:
        return

    bootstrap_cfg = profile.get("bootstrap") or {}
    routing_cfg = profile.get("routing") or {}
    metadata = profile.get("metadata") or {}

    env_map = {
        "FULLY_PRECONFIGURED": "1" if bootstrap_cfg.get("apply_initial_preferences") else "0",
        "PRECONFIGURE_API_KEYS": "1" if bootstrap_cfg.get("preconfigure_api_keys") else "0",
        "APPLY_INITIAL_PREFERENCES": "1" if bootstrap_cfg.get("apply_initial_preferences") else "0",
        "AUTO_DOWNLOAD_CONTENT": "1" if bootstrap_cfg.get("auto_download_content") else "0",
        "MEDIA_STACK_ENV": str(metadata.get("purpose", "prod")),
        "APP_GATEWAY_HOST": str(routing_cfg.get("gateway_host", "")),
        "APP_GATEWAY_PORT": str(routing_cfg.get("gateway_port", "")),
        "APP_PATH_PREFIX": str(routing_cfg.get("app_path_prefix", "/app")),
        "ROUTE_STRATEGY": str(routing_cfg.get("strategy", "hybrid")),
    }
    for key, value in env_map.items():
        if not os.environ.get(key):
            os.environ[key] = value
