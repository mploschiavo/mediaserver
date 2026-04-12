"""Validate the bootstrap profile YAML before bootstrap starts."""

from __future__ import annotations

from pathlib import Path
from typing import Any


_REQUIRED_KEYS = {
    "schema_version": int,
    "kind": str,
    "metadata": dict,
    "install_profile": str,
}

_REQUIRED_ROUTING_KEYS = {
    "strategy": str,
    "provider": str,
}


class ProfileValidationService:
    """Wraps profile validation logic."""

    def validate_profile(self, profile_file: str | None, *, log: Any = None) -> dict:
        """Validate the profile YAML and return the parsed content.

        Raises RuntimeError if validation fails.
        """

        def info(msg: str) -> None:
            if log:
                log(msg)

        if not profile_file:
            info("Profile validation: no BOOTSTRAP_PROFILE_FILE set, skipping")
            return {}

        path = Path(profile_file)
        if not path.is_file():
            raise RuntimeError(f"Profile file not found (or is a directory): {path}")

        import yaml

        with open(path) as f:
            profile = yaml.safe_load(f)

        if not isinstance(profile, dict):
            raise RuntimeError(f"Profile must be a YAML mapping, got {type(profile).__name__}")

        # Check required top-level keys.
        for key, expected_type in _REQUIRED_KEYS.items():
            value = profile.get(key)
            if value is None:
                raise RuntimeError(f"Profile missing required key: {key}")
            if not isinstance(value, expected_type):
                raise RuntimeError(
                    f"Profile key '{key}' must be {expected_type.__name__}, "
                    f"got {type(value).__name__}"
                )

        # Check metadata.
        metadata = profile["metadata"]
        if not metadata.get("name"):
            raise RuntimeError("Profile metadata.name is required")
        if not metadata.get("platform"):
            raise RuntimeError("Profile metadata.platform is required")

        # Check routing if present.
        routing = profile.get("routing")
        if isinstance(routing, dict):
            for key, expected_type in _REQUIRED_ROUTING_KEYS.items():
                value = routing.get(key)
                if value is not None and not isinstance(value, expected_type):
                    raise RuntimeError(
                        f"Profile routing.{key} must be {expected_type.__name__}"
                    )

        info(f"Profile validation: OK ({path.name})")
        return profile


_instance = ProfileValidationService()
validate_profile = _instance.validate_profile
