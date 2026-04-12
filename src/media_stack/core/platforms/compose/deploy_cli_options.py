"""Compose-target deploy CLI argument and path resolution helpers."""

from __future__ import annotations

import argparse
from pathlib import Path


class DeployCliOptionsService:
    """Wraps compose deploy CLI option helpers."""

    def register_compose_cli_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--compose-file", default=None)
        parser.add_argument("--compose-env-file", default=None)
        parser.add_argument("--compose-project-name", default=None)
        parser.add_argument("--compose-profiles", default=None)

    def resolve_compose_file_paths(
        self,
        *,
        parsed_compose_file: str | None,
        parsed_compose_env_file: str | None,
        env_compose_file: str | None,
        env_compose_env_file: str | None,
        default_compose_file: Path,
        default_compose_env_file: Path,
    ) -> tuple[Path, Path]:
        compose_file_token = str(parsed_compose_file or env_compose_file or "").strip()
        compose_env_token = str(parsed_compose_env_file or env_compose_env_file or "").strip()
        compose_file = Path(compose_file_token) if compose_file_token else default_compose_file
        compose_env_file = Path(compose_env_token) if compose_env_token else default_compose_env_file
        return compose_file, compose_env_file


_instance = DeployCliOptionsService()
register_compose_cli_arguments = _instance.register_compose_cli_arguments
resolve_compose_file_paths = _instance.resolve_compose_file_paths

__all__ = ["register_compose_cli_arguments", "resolve_compose_file_paths", "DeployCliOptionsService"]
