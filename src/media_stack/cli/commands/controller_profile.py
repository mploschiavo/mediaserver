"""Profile env setup for the bootstrap controller."""

from __future__ import annotations

import os
import pathlib

import yaml

import media_stack.services.runtime_platform as runtime_platform  # noqa: F401


_PROFILE_ENV_DEFAULTS = {
    "APP_PATH_PREFIX": "/app",
    "ROUTE_STRATEGY": "hybrid",
    "MEDIA_STACK_ENV": "prod",
}


class ControllerProfileEnvLoader:
    """Reads the bootstrap profile YAML and projects its
    ``bootstrap`` / ``routing`` / ``metadata`` sections into the
    environment variables the runtime factory expects.

    The class exposes a single public seam (``_apply_profile_env``)
    plus two private helper methods. The module-level
    ``_apply_profile_env`` alias is bound to the singleton's method
    so ``mock.patch.object(controller_profile, "_apply_profile_env", ...)``
    keeps working — the tests in
    ``tests/integration/bootstrap/test_controller_dispatch.py`` and
    ``test_controller_main_dispatch.py`` import the bare name.
    """

    def _apply_profile_env(self, profile_file: str | None) -> None:
        """Read the bootstrap profile YAML and set env vars that the
        runtime factory expects.

        Missing / unreadable profiles are silently skipped — the
        runtime factory has its own fallbacks.
        """
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
        """Parse a YAML profile file. Returns ``None`` if the file
        cannot be parsed or is empty — same semantics as the legacy
        helper this method replaced."""
        try:
            with open(path) as f:
                loaded = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            return None
        return loaded or {}

    def _build_env_map(self, profile: dict) -> dict[str, str]:
        """Project the YAML profile sections into the env var map."""
        bootstrap_cfg = profile.get("bootstrap") or {}
        routing_cfg = profile.get("routing") or {}
        metadata = profile.get("metadata") or {}

        return {
            "FULLY_PRECONFIGURED": "1" if bootstrap_cfg.get("apply_initial_preferences") else "0",
            "PRECONFIGURE_API_KEYS": "1" if bootstrap_cfg.get("preconfigure_api_keys") else "0",
            "APPLY_INITIAL_PREFERENCES": "1" if bootstrap_cfg.get("apply_initial_preferences") else "0",
            "AUTO_DOWNLOAD_CONTENT": "1" if bootstrap_cfg.get("auto_download_content") else "0",
            "MEDIA_STACK_ENV": str(metadata.get("purpose", _PROFILE_ENV_DEFAULTS["MEDIA_STACK_ENV"])),
            "APP_GATEWAY_HOST": str(routing_cfg.get("gateway_host", "")),
            "APP_GATEWAY_PORT": str(routing_cfg.get("gateway_port", "")),
            "APP_PATH_PREFIX": str(routing_cfg.get("app_path_prefix", _PROFILE_ENV_DEFAULTS["APP_PATH_PREFIX"])),
            "ROUTE_STRATEGY": str(routing_cfg.get("strategy", _PROFILE_ENV_DEFAULTS["ROUTE_STRATEGY"])),
        }


_INSTANCE = ControllerProfileEnvLoader()
_apply_profile_env = _INSTANCE._apply_profile_env


__all__ = [
    "ControllerProfileEnvLoader",
    "_apply_profile_env",
]
