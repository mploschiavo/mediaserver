#!/usr/bin/env python3
"""Build and optionally push controller image."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from media_stack.core.exceptions import ConfigError, MediaStackError

from media_stack.cli.workflows.cli_common import repo_root_from_script_file, run_command
from media_stack.core.defaults import default_controller_image


@dataclass(frozen=True)
class BuildControllerImageConfig:
    image: str
    push_image: bool
    engine: str
    dockerfile: Path
    root_dir: Path


def _truthy(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _detect_engine(preferred: str | None) -> str:
    explicit = str(preferred or "").strip().lower()
    if explicit:
        if explicit not in {"docker", "podman"}:
            raise ConfigError(f"Unsupported container engine '{explicit}'. Use docker or podman.")
        if not shutil.which(explicit):
            raise ConfigError(f"Requested engine '{explicit}' is not installed.")
        return explicit
    for candidate in ("docker", "podman"):
        if shutil.which(candidate):
            return candidate
    raise ConfigError("Neither docker nor podman was found in PATH.")


def parse_config(argv: list[str] | None = None) -> BuildControllerImageConfig:
    root_dir = repo_root_from_script_file(__file__)
    parser = argparse.ArgumentParser(
        prog="bin/build-controller-image.sh",
        description="Build controller image used by k8s/controller.yaml, docker-compose.yml, and CronJobs.",
    )
    parser.add_argument(
        "--image",
        default=default_controller_image(),
    )
    push_default = _truthy(os.environ.get("PUSH_IMAGE"), True)
    parser.add_argument("--push", dest="push_image", action="store_true", default=push_default)
    parser.add_argument("--no-push", dest="push_image", action="store_false")
    parser.add_argument("--engine", default=os.environ.get("CONTAINER_ENGINE", ""))
    parser.add_argument(
        "--dockerfile",
        default=os.environ.get(
            "DOCKERFILE", str(root_dir / "docker" / "controller.Dockerfile")
        ),
    )
    args = parser.parse_args(argv)

    image = str(args.image or "").strip()
    if not image:
        raise ConfigError("Image reference cannot be empty.")
    dockerfile = Path(str(args.dockerfile or "")).expanduser().resolve()
    if not dockerfile.is_file():
        raise ConfigError(f"Dockerfile not found: {dockerfile}")
    engine = _detect_engine(str(args.engine or "").strip())
    return BuildControllerImageConfig(
        image=image,
        push_image=bool(args.push_image),
        engine=engine,
        dockerfile=dockerfile,
        root_dir=root_dir,
    )


def run(cfg: BuildControllerImageConfig) -> int:
    run_command(
        [
            cfg.engine,
            "build",
            "-f",
            str(cfg.dockerfile),
            "-t",
            cfg.image,
            str(cfg.root_dir),
        ]
    )
    if cfg.push_image:
        run_command([cfg.engine, "push", cfg.image])

    print(f"Built controller image: {cfg.image}")
    if cfg.push_image:
        print(f"Pushed controller image: {cfg.image}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = parse_config(argv)
        return run(cfg)
    except (ConfigError, MediaStackError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
