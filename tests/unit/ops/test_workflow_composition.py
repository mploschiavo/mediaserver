"""Tests for the CLI workflow composition root."""

from __future__ import annotations

from pathlib import Path

from media_stack.cli.workflows.workflow_composition_service import WorkflowCompositionService


class FakeCommandRunner:
    """Command runner identity used to verify dependency injection."""

    def run_text(self, command, *, env=None, check=True):
        return ""

    def run_json(self, command, *, env=None, check=True):
        return {}


class TestWorkflowCompositionService:
    """Release, deploy, and teardown dependencies are composed centrally."""

    def test_reuses_injected_command_runner_across_release_and_teardown(self, tmp_path: Path) -> None:
        runner = FakeCommandRunner()
        composition = WorkflowCompositionService(tmp_path, command_runner=runner)
        assert composition.release_version_policy().command_runner is runner
        assert composition.release_image_builder().command_runner is runner
        assert composition.release_compose_deployer().command_runner is runner
        assert composition.release_kubernetes_deployer().command_runner is runner
        assert composition.teardown_planner().command_runner is runner
        assert composition.teardown_executor(assume_yes=True).command_runner is runner

    def test_deploy_config_is_exposed_from_composition_root(self, tmp_path: Path) -> None:
        composition = WorkflowCompositionService(tmp_path, command_runner=FakeCommandRunner())
        config = composition.deploy_stack_config([])
        assert config.root_dir == tmp_path
