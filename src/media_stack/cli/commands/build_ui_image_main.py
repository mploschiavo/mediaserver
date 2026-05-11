#!/usr/bin/env python3
"""Entry-point shim for ``bin/build/build-ui-image.sh``.

ADR-0015 Phase 6 moved the build logic + the
:class:`BuildUIImageConfig` dataclass + ``default_ui_image`` /
``_read_ui_version`` to
:mod:`media_stack.cli.workflows.build_ui_image_service`. This
module holds only the argparse → dataclass conversion (the
"per-CLI argument parsing" the ADR boundary contract reserves for
``cli/commands/``).

The UI image is versioned independently of the API/controller
(see ``VERSION-UI``) so the dashboard can iterate without forcing
a controller rebuild.
"""

from __future__ import annotations

import argparse
import os
import shutil  # noqa: F401 — re-exported so legacy patches against ``module.shutil`` resolve
import sys
from pathlib import Path

from media_stack.cli.workflows.build_ui_image_service import (
    BuildUIImageConfig,
    BuildUIImageService,
    _detect_engine,
    _read_ui_version,
    _truthy,
    default_ui_image,
    run,
)
from media_stack.core.cli_common import (
    repo_root_from_script_file,
    run_command,  # noqa: F401 — re-exported for legacy patches
)
from media_stack.core.exceptions import ConfigError, MediaStackError


class BuildUIImageEntryPoint:
    """Per-ADR-0012 entry-point: argv → cfg → service.run → exit code."""

    def __init__(self) -> None:
        self._service = BuildUIImageService()

    def parse_config(self, argv: list[str] | None = None) -> BuildUIImageConfig:
        module = sys.modules[__name__]
        root_dir = repo_root_from_script_file(__file__)
        parser = argparse.ArgumentParser(
            prog="bin/build-ui-image.sh",
            description=(
                "Build the nginx UI image (dashboard + static assets, "
                "/api/* reverse-proxied)."
            ),
        )
        parser.add_argument(
            "--image",
            default=module.default_ui_image(root_dir),
        )
        push_default = module._truthy(os.environ.get("PUSH_IMAGE"), True)
        parser.add_argument(
            "--push", dest="push_image", action="store_true", default=push_default,
        )
        parser.add_argument("--no-push", dest="push_image", action="store_false")
        parser.add_argument("--engine", default=os.environ.get("CONTAINER_ENGINE", ""))
        parser.add_argument(
            "--dockerfile",
            default=os.environ.get(
                "DOCKERFILE", str(root_dir / "deploy" / "compose" / "ui.Dockerfile")
            ),
        )
        args = parser.parse_args(argv)

        image = str(args.image or "").strip()
        if not image:
            raise ConfigError("Image reference cannot be empty.")
        dockerfile = Path(str(args.dockerfile or "")).expanduser().resolve()
        if not dockerfile.is_file():
            raise ConfigError(f"Dockerfile not found: {dockerfile}")
        engine = module._detect_engine(str(args.engine or "").strip())
        return BuildUIImageConfig(
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


_INSTANCE = BuildUIImageEntryPoint()
parse_config = _INSTANCE.parse_config
main = _INSTANCE.main


__all__ = [
    "BuildUIImageConfig",
    "BuildUIImageEntryPoint",
    "_detect_engine",
    "_read_ui_version",
    "_truthy",
    "default_ui_image",
    "main",
    "parse_config",
    "run",
]


if __name__ == "__main__":
    raise SystemExit(_INSTANCE.main())
