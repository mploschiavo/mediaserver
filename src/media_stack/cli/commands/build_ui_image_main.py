#!/usr/bin/env python3
"""Build and optionally push the nginx-served UI image.

The UI image is versioned independently of the API/controller (see
``VERSION-UI`` at the repo root) so the dashboard can iterate without
forcing a controller rebuild.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from media_stack.core.exceptions import ConfigError, MediaStackError

from media_stack.core.cli_common import repo_root_from_script_file, run_command


# Image coordinates are duplicated from the profile YAML's controller block
# pattern (registry / image_name / image_tag). The UI does not yet have a
# dedicated profile section so we hard-default the registry; an operator can
# override with --image or BOOTSTRAP_UI_IMAGE.
_DEFAULT_REGISTRY = "harbor.iomio.io/library"
_DEFAULT_IMAGE_NAME = "media-stack-ui"


@dataclass(frozen=True)
class BuildUIImageConfig:
    image: str
    push_image: bool
    engine: str
    dockerfile: Path
    root_dir: Path


class BuildUiImageCommand:
    """Build (and optionally push) the nginx-served UI image.

    All helpers live as instance methods so the module's top-level
    ``FunctionDef`` count stays at zero per ADR-0012. Cross-helper calls
    dispatch through ``sys.modules[__name__]`` so ``mock.patch`` of the
    module-level alias still intercepts the helper.
    """

    def _read_ui_version(self, root_dir: Path) -> str:
        """Return the contents of VERSION-UI, or 'dev' if unreadable.

        Kept lenient -- the build step itself will surface a hard failure if
        the image tag we synthesize is unusable, and tests/CI may run without
        the file populated.
        """
        version_file = root_dir / "VERSION-UI"
        try:
            text = version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return "dev"
        return text or "dev"

    def default_ui_image(self, root_dir: Path) -> str:
        """Resolve the default UI image ref. Env override wins for parity
        with the controller's BOOTSTRAP_RUNNER_IMAGE escape hatch."""
        env = os.environ.get("BOOTSTRAP_UI_IMAGE", "").strip()
        if env:
            return env
        version = sys.modules[__name__]._read_ui_version(root_dir)
        return f"{_DEFAULT_REGISTRY}/{_DEFAULT_IMAGE_NAME}:v{version}"

    def _truthy(self, value: str | None, default: bool) -> bool:
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _detect_engine(self, preferred: str | None) -> str:
        explicit = str(preferred or "").strip().lower()
        if explicit:
            if explicit not in {"docker", "podman"}:
                raise ConfigError(
                    f"Unsupported container engine '{explicit}'. Use docker or podman."
                )
            if not shutil.which(explicit):
                raise ConfigError(f"Requested engine '{explicit}' is not installed.")
            return explicit
        for candidate in ("docker", "podman"):
            if shutil.which(candidate):
                return candidate
        raise ConfigError("Neither docker nor podman was found in PATH.")

    def parse_config(self, argv: list[str] | None = None) -> BuildUIImageConfig:
        module = sys.modules[__name__]
        root_dir = repo_root_from_script_file(__file__)
        parser = argparse.ArgumentParser(
            prog="bin/build-ui-image.sh",
            description="Build the nginx UI image (dashboard + static assets, /api/* reverse-proxied).",
        )
        parser.add_argument(
            "--image",
            default=module.default_ui_image(root_dir),
        )
        push_default = module._truthy(os.environ.get("PUSH_IMAGE"), True)
        parser.add_argument(
            "--push", dest="push_image", action="store_true", default=push_default
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

    def run(self, cfg: BuildUIImageConfig) -> int:
        # Sanity check: the multi-stage Dockerfile's build stage runs
        # `pnpm install --frozen-lockfile`, which requires ui/pnpm-lock.yaml
        # to exist. Surface a clear error before invoking docker build so
        # operators don't have to read a Buildkit log to find the cause.
        lockfile = cfg.root_dir / "ui" / "pnpm-lock.yaml"
        if not lockfile.is_file():
            print(
                f"error: {lockfile} not found — run `pnpm install` in ui/ first.",
                file=sys.stderr,
            )
            return 1

        # Build context is the repo root: the Dockerfile's build stage
        # COPY-s ui/ AND contracts/api/openapi.yaml, both of which
        # are siblings under cfg.root_dir.
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

        print(f"Built UI image: {cfg.image}")
        if cfg.push_image:
            print(f"Pushed UI image: {cfg.image}")
        return 0

    def main(self, argv: list[str] | None = None) -> int:
        module = sys.modules[__name__]
        try:
            cfg = module.parse_config(argv)
            return module.run(cfg)
        except (ConfigError, MediaStackError, OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1


_INSTANCE = BuildUiImageCommand()
_read_ui_version = _INSTANCE._read_ui_version
default_ui_image = _INSTANCE.default_ui_image
_truthy = _INSTANCE._truthy
_detect_engine = _INSTANCE._detect_engine
parse_config = _INSTANCE.parse_config
run = _INSTANCE.run
main = _INSTANCE.main


if __name__ == "__main__":
    raise SystemExit(_INSTANCE.main())
