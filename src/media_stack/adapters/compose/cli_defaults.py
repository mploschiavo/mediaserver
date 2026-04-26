"""Compose-scoped CLI default values."""

from __future__ import annotations

from pathlib import Path

from media_stack.core.platform_cli_defaults_registry import PlatformCliDefaults


class ComposeCliDefaultsService:
    """Wraps compose CLI defaults resolution."""

    def resolve_cli_defaults(self, root_dir: Path) -> PlatformCliDefaults:
        return PlatformCliDefaults(
            compose_file=root_dir / "deploy" / "compose" / "docker-compose.yml",
            compose_env_file=root_dir / "deploy" / "compose" / ".env",
        )


_instance = ComposeCliDefaultsService()
resolve_cli_defaults = _instance.resolve_cli_defaults
