"""Composition root for CLI workflow services."""

from __future__ import annotations

from pathlib import Path

from media_stack.cli.workflows.deploy_cli_config_service import DeployStackConfig, parse_deploy_stack_config
from media_stack.cli.workflows.release_compose_deploy_service import ReleaseComposeDeployService
from media_stack.cli.workflows.release_image_build_service import ReleaseImageBuildService
from media_stack.cli.workflows.release_kubernetes_deploy_service import ReleaseKubernetesDeployService
from media_stack.cli.workflows.release_pipeline_config_service import ReleasePipelineConfigService
from media_stack.cli.workflows.release_version_policy_service import ReleaseVersionPolicyService
from media_stack.cli.workflows.teardown_executor_service import TeardownExecutorFactory, TeardownExecutorService
from media_stack.cli.workflows.teardown_plan_service import TeardownPlanService
from media_stack.cli.workflows.workflow_command_runner_service import WorkflowCommandRunnerService
from media_stack.cli.workflows.workflow_interfaces import CommandRunner


class WorkflowCompositionService:
    """Wires workflow dependencies in one place."""

    def __init__(self, root_dir: Path, command_runner: CommandRunner | None = None) -> None:
        self.root_dir = root_dir
        self.command_runner = command_runner or WorkflowCommandRunnerService()

    def release_config(self) -> ReleasePipelineConfigService:
        return ReleasePipelineConfigService(self.root_dir)

    def release_version_policy(self) -> ReleaseVersionPolicyService:
        return ReleaseVersionPolicyService(self.root_dir, self.command_runner)

    def release_image_builder(self) -> ReleaseImageBuildService:
        return ReleaseImageBuildService(self.root_dir, self.command_runner)

    def release_compose_deployer(self) -> ReleaseComposeDeployService:
        return ReleaseComposeDeployService(self.root_dir, self.command_runner)

    def release_kubernetes_deployer(self) -> ReleaseKubernetesDeployService:
        return ReleaseKubernetesDeployService(self.command_runner)

    def deploy_stack_config(self, argv: list[str]) -> DeployStackConfig:
        return parse_deploy_stack_config(argv, root_dir=self.root_dir)

    def teardown_planner(self) -> TeardownPlanService:
        return TeardownPlanService(self.command_runner)

    def teardown_executor(self, *, assume_yes: bool) -> TeardownExecutorService:
        return TeardownExecutorFactory().create(self.command_runner, assume_yes=assume_yes)
