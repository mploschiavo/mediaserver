"""Build and publish controller/UI release images."""

from __future__ import annotations

import json
from pathlib import Path

from media_stack.cli.commands.build_controller_image_main import (
    parse_config as parse_controller_build_config,
    run as run_controller_build,
)
from media_stack.cli.commands.build_ui_image_main import parse_config as parse_ui_build_config
from media_stack.cli.commands.build_ui_image_main import run as run_ui_build
from media_stack.cli.workflows.workflow_command_runner_service import WorkflowCommandRunnerService
from media_stack.cli.workflows.workflow_interfaces import CommandRunner
from media_stack.cli.workflows.release_pipeline_config_service import ReleasePipelineConfigService
from media_stack.cli.workflows.release_pipeline_models import ReleaseBuildResult, ReleaseImageRefs
from media_stack.core.cli_common import info
from media_stack.core.exceptions import MediaStackError


class ReleaseImageBuildService:
    """Builds both release images and records local repo digests."""

    def __init__(
        self,
        root_dir: Path,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.command_runner = command_runner or WorkflowCommandRunnerService()
        self.config = ReleasePipelineConfigService(root_dir)

    def build(self, refs: ReleaseImageRefs, *, no_push: bool = False) -> ReleaseBuildResult:
        push_args = ["--no-push"] if no_push else ["--push"]
        info(f"Building controller image: {refs.controller_image}")
        controller_rc = run_controller_build(
            parse_controller_build_config(["--image", refs.controller_image, *push_args])
        )
        if controller_rc != 0:
            raise MediaStackError(f"Controller image build failed with exit code {controller_rc}")

        info(f"Building UI image: {refs.ui_image}")
        ui_rc = run_ui_build(parse_ui_build_config(["--image", refs.ui_image, *push_args]))
        if ui_rc != 0:
            raise MediaStackError(f"UI image build failed with exit code {ui_rc}")

        return ReleaseBuildResult(
            controller_image=refs.controller_image,
            controller_digest=self.inspect_repo_digest(refs.controller_image),
            ui_image=refs.ui_image,
            ui_digest=self.inspect_repo_digest(refs.ui_image),
            controller_version=refs.controller_version,
            ui_version=refs.ui_version,
        )

    def inspect_repo_digest(self, image: str) -> str:
        raw = self.command_runner.run_text(
            ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"]
        )
        digests = json.loads(raw) if raw else []
        if not digests:
            return ""
        first = str(digests[0])
        return first.split("@", 1)[1] if "@sha256:" in first else ""

    def write_artifact(self, result: ReleaseBuildResult, output_json: str) -> None:
        if not output_json:
            return
        target = Path(output_json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result.__dict__, indent=2, sort_keys=True), encoding="utf-8")
        info(f"Wrote build artifact: {target}")
