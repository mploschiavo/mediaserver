"""Build (and optionally push) the nginx-served UI container image.

ADR-0015 Phase 6: the build LOGIC (dockerfile resolution, lockfile
sanity check, engine detection, ``docker build`` + ``docker push``)
lives in the workflows tier so other workflows
(:mod:`release_image_build_service`,
:mod:`release_pipeline_config_service`) can compose it without
crossing the cli/commands → cli/workflows boundary the way
pre-Phase-6 code did.

The argparse entry-point in :mod:`cli.commands.build_ui_image_main`
remains the only place argv translates into a
:class:`BuildUIImageConfig`; this module owns everything downstream
of that conversion.

The UI image is versioned independently of the API/controller
(see ``VERSION-UI`` at the repo root) so the dashboard can iterate
without forcing a controller rebuild.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from media_stack.core.cli_common import run_command
from media_stack.core.exceptions import ConfigError


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


class BuildUIImageService:
    """Builder: resolve UI version + engine, run docker build, optionally push."""

    def read_ui_version(self, root_dir: Path) -> str:
        """Return the contents of VERSION-UI, or 'dev' if unreadable.

        Kept lenient -- the build step itself will surface a hard
        failure if the image tag we synthesize is unusable, and
        tests/CI may run without the file populated.
        """
        version_file = root_dir / "VERSION-UI"
        try:
            text = version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return "dev"
        return text or "dev"

    def default_ui_image(self, root_dir: Path) -> str:
        """Resolve the default UI image ref.

        ``BOOTSTRAP_UI_IMAGE`` env override wins for parity with the
        controller's ``BOOTSTRAP_RUNNER_IMAGE`` escape hatch. Otherwise
        synthesise from the repo's ``VERSION-UI`` text.
        """
        env = os.environ.get("BOOTSTRAP_UI_IMAGE", "").strip()
        if env:
            return env
        version = sys.modules[__name__]._read_ui_version(root_dir)
        return f"{_DEFAULT_REGISTRY}/{_DEFAULT_IMAGE_NAME}:v{version}"

    def truthy(self, value: str | None, default: bool) -> bool:
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def detect_engine(self, preferred: str | None) -> str:
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

    def build_config(
        self,
        *,
        image: str,
        push_image: bool,
        root_dir: Path,
        dockerfile: Path | None = None,
        engine: str = "",
    ) -> BuildUIImageConfig:
        """Direct construction path for the release workflow.

        The argparse entry-point in commands/ wraps this; release-pipeline
        callers that already have a config-shaped argument bundle skip the
        argparse step and build the config inline.
        """
        image = str(image or "").strip()
        if not image:
            raise ConfigError("Image reference cannot be empty.")
        resolved_dockerfile = (
            dockerfile.expanduser().resolve()
            if dockerfile is not None
            else (root_dir / "deploy" / "compose" / "ui.Dockerfile")
        )
        if not resolved_dockerfile.is_file():
            raise ConfigError(f"Dockerfile not found: {resolved_dockerfile}")
        return BuildUIImageConfig(
            image=image,
            push_image=bool(push_image),
            engine=self.detect_engine(engine),
            dockerfile=resolved_dockerfile,
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


# Module-level singleton + aliases — ADR-0012 rule 10. The argparse
# entry-point and tests refer to ``_read_ui_version`` /
# ``default_ui_image`` / ``_truthy`` / ``_detect_engine`` / ``run``
# at module scope; preserve those names here.
_INSTANCE = BuildUIImageService()
_read_ui_version = _INSTANCE.read_ui_version
default_ui_image = _INSTANCE.default_ui_image
_truthy = _INSTANCE.truthy
_detect_engine = _INSTANCE.detect_engine
build_config = _INSTANCE.build_config
run = _INSTANCE.run


__all__ = [
    "BuildUIImageConfig",
    "BuildUIImageService",
    "build_config",
    "default_ui_image",
    "run",
]
