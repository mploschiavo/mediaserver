#!/usr/bin/env python3
"""Entry-point shim for ``bin/build/build-controller-image.sh``.

ADR-0015 Phase 6 moved the build logic + the
:class:`BuildControllerImageConfig` dataclass to
:mod:`media_stack.cli.workflows.build_controller_image_service`.
This module holds only the argparse → dataclass conversion (the
"per-CLI argument parsing" the ADR boundary contract reserves for
``cli/commands/``).

The module-level ``parse_config`` / ``run`` / ``main`` /
``truthy`` / ``detect_engine`` names + their leading-underscore
aliases survive for the pre-Phase-6 test surface (existing
patches against this module path keep intercepting).
"""

from __future__ import annotations

import argparse
import os
import shutil  # noqa: F401 — re-exported so legacy patches against ``module.shutil`` resolve
import sys
from pathlib import Path

from media_stack.cli.workflows.build_controller_image_service import (
    BuildControllerImageConfig,
    BuildControllerImageService,
    detect_engine,
    run,
    truthy,
)
from media_stack.core.cli_common import repo_root_from_script_file
from media_stack.core.defaults import default_controller_image
from media_stack.core.exceptions import ConfigError, MediaStackError


class BuildControllerImageEntryPoint:
    """Per-ADR-0012 entry-point: argv → cfg → service.run → exit code."""

    def __init__(self) -> None:
        self._service = BuildControllerImageService()

    def parse_config(self, argv: list[str] | None = None) -> BuildControllerImageConfig:
        module = sys.modules[__name__]
        root_dir = repo_root_from_script_file(__file__)
        parser = argparse.ArgumentParser(
            prog="bin/build-controller-image.sh",
            description=(
                "Build controller image used by deploy/k8s/base/controller/controller.yaml, "
                "deploy/compose/docker-compose.yml, and CronJobs."
            ),
        )
        parser.add_argument(
            "--image",
            default=default_controller_image(),
        )
        push_default = module.truthy(os.environ.get("PUSH_IMAGE"), True)
        parser.add_argument(
            "--push", dest="push_image", action="store_true", default=push_default,
        )
        parser.add_argument("--no-push", dest="push_image", action="store_false")
        parser.add_argument("--engine", default=os.environ.get("CONTAINER_ENGINE", ""))
        parser.add_argument(
            "--dockerfile",
            default=os.environ.get(
                "DOCKERFILE", str(root_dir / "deploy" / "compose" / "controller.Dockerfile")
            ),
        )
        args = parser.parse_args(argv)

        image = str(args.image or "").strip()
        if not image:
            raise ConfigError("Image reference cannot be empty.")
        dockerfile = Path(str(args.dockerfile or "")).expanduser().resolve()
        if not dockerfile.is_file():
            raise ConfigError(f"Dockerfile not found: {dockerfile}")
        engine = module.detect_engine(str(args.engine or "").strip())
        return BuildControllerImageConfig(
            image=image,
            push_image=bool(args.push_image),
            engine=engine,
            dockerfile=dockerfile,
            root_dir=root_dir,
        )

    def main(self, argv: list[str] | None = None) -> int:
        module = sys.modules[__name__]
        try:
            cfg = module.parse_config(argv)
            return module.run(cfg)
        except (ConfigError, MediaStackError, OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1


_INSTANCE = BuildControllerImageEntryPoint()
parse_config = _INSTANCE.parse_config
main = _INSTANCE.main
# Re-export the workflows-tier names at this module path so existing
# patches (``mock.patch("media_stack.cli.commands.build_controller_image_main.truthy")``)
# keep intercepting. The underscore aliases below match the
# leading-underscore convention that the pre-Phase-6 module used.
_truthy = truthy
_detect_engine = detect_engine


__all__ = [
    "BuildControllerImageConfig",
    "BuildControllerImageEntryPoint",
    "detect_engine",
    "main",
    "parse_config",
    "run",
    "truthy",
]


if __name__ == "__main__":
    raise SystemExit(main())
