"""Compose-scoped CLI default values."""

from __future__ import annotations

from pathlib import Path

from media_stack.core.platform_cli_defaults_registry import PlatformCliDefaults


def resolve_cli_defaults(root_dir: Path) -> PlatformCliDefaults:
    return PlatformCliDefaults(
        compose_file=root_dir / "docker" / "docker-compose.yml",
        compose_env_file=root_dir / "docker" / ".env",
    )
