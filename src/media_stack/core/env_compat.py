"""Backward-compatible environment variable access.

Supports the BOOTSTRAP_ → CONTROLLER_ rename. Checks the new name first,
falls back to the old name. This allows gradual migration of env vars
in K8s manifests, Docker Compose files, and CI scripts.
"""
from __future__ import annotations

import os

# Mapping: new name → old name
_RENAME_MAP = {
    "CONTROLLER_PROFILE_FILE": "BOOTSTRAP_PROFILE_FILE",
    "CONTROLLER_CONFIG_FILE": "BOOTSTRAP_CONFIG_FILE",
    "CONTROLLER_API_PORT": "BOOTSTRAP_API_PORT",
    "CONTROLLER_WAIT_INTERVAL_SECONDS": "BOOTSTRAP_WAIT_INTERVAL_SECONDS",
    "CONTROLLER_WAIT_HEARTBEAT_SECONDS": "BOOTSTRAP_WAIT_HEARTBEAT_SECONDS",
    "CONTROLLER_WAIT_TIMEOUT": "BOOTSTRAP_WAIT_TIMEOUT",
    "CONTROLLER_ACTION_TIMEOUT": "BOOTSTRAP_ACTION_TIMEOUT",
    "CONTROLLER_ACTION_MAX_RETRIES": "BOOTSTRAP_ACTION_MAX_RETRIES",
    "CONTROLLER_RUN_PREFLIGHTS": "BOOTSTRAP_RUN_PREFLIGHTS",
    "CONTROLLER_IMAGE": "BOOTSTRAP_RUNNER_IMAGE",
    "CONTROLLER_RESUME": "BOOTSTRAP_RESUME",
    "CONTROLLER_STATE_FILE": "BOOTSTRAP_STATE_FILE",
    "CONTROLLER_DEBUG": "BOOTSTRAP_DEBUG",
    "CONTROLLER_API_MODE": "BOOTSTRAP_API_MODE",
    "CONTROLLER_IMAGE_PULL_POLICY": "BOOTSTRAP_IMAGE_PULL_POLICY",
    "CONTROLLER_PROFILE_CATALOG_FILE": "BOOTSTRAP_PROFILE_CATALOG_FILE",
    "CONTROLLER_PURPOSE": "BOOTSTRAP_PURPOSE",
}

# Reverse lookup
_REVERSE_MAP = {v: k for k, v in _RENAME_MAP.items()}


class EnvCompat:
    """Environment variable access with backward compatibility."""

    def get(self, key: str, default: str = "") -> str:
        """Get env var. If key is BOOTSTRAP_*, also checks CONTROLLER_ equivalent and vice versa."""
        # Direct lookup first
        val = os.environ.get(key, "").strip()
        if val:
            return val
        # Check rename mapping
        if key.startswith("CONTROLLER_"):
            old_key = _RENAME_MAP.get(key)
            if old_key:
                val = os.environ.get(old_key, "").strip()
                if val:
                    return val
        elif key.startswith("BOOTSTRAP_"):
            new_key = _REVERSE_MAP.get(key)
            if new_key:
                val = os.environ.get(new_key, "").strip()
                if val:
                    return val
        return default


_instance = EnvCompat()
get = _instance.get
