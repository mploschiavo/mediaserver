"""Docker Compose release deploy and verification service."""

from __future__ import annotations

import time
from pathlib import Path

from media_stack.cli.workflows.workflow_command_runner_service import WorkflowCommandRunnerService
from media_stack.cli.workflows.workflow_interfaces import CommandRunner
from media_stack.cli.workflows.release_pipeline_config_service import ReleasePipelineConfigService
from media_stack.cli.workflows.release_pipeline_models import ComposeVerificationResult, ReleaseImageRefs
from media_stack.core.cli_common import info
from media_stack.core.exceptions import MediaStackError


class ReleaseComposeDeployService:
    """Deploys and verifies Compose controller/UI images together."""

    def __init__(
        self,
        root_dir: Path,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.command_runner = command_runner or WorkflowCommandRunnerService()
        self.config = ReleasePipelineConfigService(root_dir)

    def deploy(
        self,
        refs: ReleaseImageRefs,
        *,
        controller_health_url: str,
        ui_health_url: str,
        health_timeout_seconds: int,
    ) -> ComposeVerificationResult:
        compose_file = self.config.compose_file()
        env = self.compose_env(refs)
        info("Pulling pinned Compose controller/UI images")
        self.command_runner.run_text(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "pull",
                "media-stack-controller",
                "media-stack-ui",
            ],
            env=env,
        )
        info("Recreating Compose controller/UI services")
        self.command_runner.run_text(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "up",
                "-d",
                "--force-recreate",
                "media-stack-controller",
                "media-stack-ui",
            ],
            env=env,
        )
        self.wait_http(controller_health_url, health_timeout_seconds)
        self.wait_http(ui_health_url, health_timeout_seconds)
        return self.verify(refs)

    def verify(self, refs: ReleaseImageRefs) -> ComposeVerificationResult:
        result = ComposeVerificationResult(
            expected_controller_image=refs.controller_image,
            expected_ui_image=refs.ui_image,
            running_controller_image=self.container_image("media-stack-controller"),
            running_ui_image=self.container_image("media-stack-ui"),
            controller_image_id=self.container_image_id("media-stack-controller"),
            ui_image_id=self.container_image_id("media-stack-ui"),
        )
        if not result.passed:
            raise MediaStackError("Compose verification failed: running images do not match release refs.")
        return result

    def compose_env(self, refs: ReleaseImageRefs) -> dict[str, str]:
        return {
            "BOOTSTRAP_RUNNER_IMAGE": refs.controller_image,
            "UI_RUNNER_IMAGE": refs.ui_image,
        }

    def container_image(self, container_name: str) -> str:
        return self.command_runner.run_text(
            ["docker", "inspect", container_name, "--format", "{{.Config.Image}}"]
        )

    def container_image_id(self, container_name: str) -> str:
        return self.command_runner.run_text(
            ["docker", "inspect", container_name, "--format", "{{.Image}}"]
        )

    def wait_http(self, url: str, timeout_seconds: int) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                self.command_runner.run_text(["curl", "-fsS", url])
                return
            except Exception:
                time.sleep(3)
        raise MediaStackError(f"Timed out waiting for {url}")
