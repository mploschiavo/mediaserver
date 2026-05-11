"""Build (and optionally push) the controller container image.

ADR-0015 Phase 6: the build LOGIC (dockerfile resolution, engine
detection, ``docker build`` + ``docker push`` invocation) lives in
the workflows tier so other workflows
(:mod:`release_image_build_service`) can compose it without
crossing the cli/commands → cli/workflows boundary the way
pre-Phase-6 code did.

The argparse entry-point in :mod:`cli.commands.build_controller_image_main`
remains the only place argv translates into a
:class:`BuildControllerImageConfig`; this module owns everything
downstream of that conversion.

Pattern: Builder (Gang-of-Four). One immutable
:class:`BuildControllerImageConfig` describes the build, one
:class:`BuildControllerImageService` executes it. The Service
instance is stateless aside from the cfg it's handed, so the
commands-tier entry-point can share a process-global singleton
without coupling consumers.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from media_stack.core.cli_common import run_command
from media_stack.core.exceptions import ConfigError


@dataclass(frozen=True)
class BuildControllerImageConfig:
    image: str
    push_image: bool
    engine: str
    dockerfile: Path
    root_dir: Path


class BuildControllerImageService:
    """Builder: resolve engine, run docker build, optionally push."""

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
    ) -> BuildControllerImageConfig:
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
            else (root_dir / "deploy" / "compose" / "controller.Dockerfile")
        )
        if not resolved_dockerfile.is_file():
            raise ConfigError(f"Dockerfile not found: {resolved_dockerfile}")
        return BuildControllerImageConfig(
            image=image,
            push_image=bool(push_image),
            engine=self.detect_engine(engine),
            dockerfile=resolved_dockerfile,
            root_dir=root_dir,
        )

    def run(self, cfg: BuildControllerImageConfig) -> int:
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


# Module-level singleton + aliases — ADR-0012 rule 10. Tests +
# ``cli.commands.build_controller_image_main`` patch the
# ``truthy`` / ``detect_engine`` names; preserve that surface here
# instead of inside the entry-point shim.
_INSTANCE = BuildControllerImageService()
truthy = _INSTANCE.truthy
detect_engine = _INSTANCE.detect_engine
build_config = _INSTANCE.build_config
run = _INSTANCE.run


__all__ = [
    "BuildControllerImageConfig",
    "BuildControllerImageService",
    "build_config",
    "detect_engine",
    "run",
    "truthy",
]
