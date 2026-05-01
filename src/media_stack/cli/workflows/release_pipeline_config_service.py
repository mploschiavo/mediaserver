"""Configuration and version helpers for release workflows."""

from __future__ import annotations

import json
from pathlib import Path

from media_stack.cli.commands.build_ui_image_main import default_ui_image
from media_stack.cli.workflows.release_pipeline_models import ReleaseImageRefs
from media_stack.core.defaults import default_controller_image


class ReleaseImageReferenceService:
    """Builds immutable release image refs from project versions."""

    def controller_image_for_version(self, version: str) -> str:
        base = default_controller_image().strip()
        name = base.rsplit(":", 1)[0] if ":" in base.rsplit("/", 1)[-1] else base
        return f"{name}:v{version}"


class ReleasePipelineConfigService:
    """Resolves repo paths, versions, and default image references."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.image_refs = ReleaseImageReferenceService()

    def read_text(self, relative_path: str) -> str:
        return (self.root_dir / relative_path).read_text(encoding="utf-8").strip()

    def controller_version(self) -> str:
        return self.read_text("VERSION")

    def ui_version(self) -> str:
        return self.read_text("VERSION-UI")

    def ui_package_version(self) -> str:
        data = json.loads((self.root_dir / "ui" / "package.json").read_text(encoding="utf-8"))
        return str(data.get("version", ""))

    def compose_file(self) -> Path:
        return self.root_dir / "deploy" / "compose" / "docker-compose.yml"

    def release_image_refs(self, controller_image: str = "", ui_image: str = "") -> ReleaseImageRefs:
        return ReleaseImageRefs(
            controller_image=controller_image.strip()
            or self.image_refs.controller_image_for_version(self.controller_version()),
            ui_image=ui_image.strip() or default_ui_image(self.root_dir),
            controller_version=self.controller_version(),
            ui_version=self.ui_version(),
        )
