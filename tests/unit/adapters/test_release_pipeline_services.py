"""Unit tests for Python-first release pipeline services."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_stack.cli.workflows.release_compose_deploy_service import ReleaseComposeDeployService
from media_stack.cli.workflows.release_kubernetes_deploy_service import ReleaseKubernetesDeployService
from media_stack.cli.workflows.release_pipeline_models import ReleaseImageRefs
from media_stack.cli.workflows.release_version_policy_service import ReleaseVersionPolicyService
from media_stack.core.exceptions import MediaStackError


class FakeCommandRunner:
    """Tiny command runner test double keyed by argv tuples."""

    def __init__(self, responses: dict[tuple[str, ...], str | dict]) -> None:
        self.responses = responses
        self.commands: list[tuple[str, ...]] = []

    def run_text(self, command, *, env=None, check=True):
        key = tuple(command)
        self.commands.append(key)
        value = self.responses.get(key, "")
        if isinstance(value, dict):
            return json.dumps(value)
        return value

    def run_json(self, command, *, env=None, check=True):
        raw = self.run_text(command, env=env, check=check)
        return json.loads(raw) if raw else {}


class ReleasePipelineFixture:
    """Builds a minimal repo tree for release policy tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "ui").mkdir()
        (root / "VERSION").write_text("1.0.285\n", encoding="utf-8")
        (root / "VERSION-UI").write_text("1.3.68\n", encoding="utf-8")
        (root / "ui" / "package.json").write_text(
            json.dumps({"version": "1.3.68"}),
            encoding="utf-8",
        )

    def policy_runner(self, *, changed: str) -> FakeCommandRunner:
        return FakeCommandRunner(
            {
                ("git", "diff", "--name-only", "origin/main...HEAD"): "",
                ("git", "diff", "--name-only"): changed,
                ("git", "diff", "--cached", "--name-only"): "",
                ("git", "ls-files", "--others", "--exclude-standard"): "",
                ("git", "show", "origin/main:VERSION-UI"): "1.3.67",
                ("git", "show", "origin/main:VERSION"): "1.0.284",
                ("git", "show", "origin/main:ui/package.json"): json.dumps({"version": "1.3.67"}),
            }
        )


class TestReleaseVersionPolicyService:
    """Version policy catches dirty-tree release tag reuse."""

    def test_dirty_ui_change_requires_ui_version_files(self, tmp_path: Path) -> None:
        fixture = ReleasePipelineFixture(tmp_path)
        runner = fixture.policy_runner(changed="ui/src/App.tsx\n")
        result = ReleaseVersionPolicyService(tmp_path, runner).check("origin/main")
        assert result.ui_changed is True
        assert "UI source changed but VERSION-UI and ui/package.json" in "\n".join(result.issues)

    def test_dirty_controller_change_requires_controller_version_files(self, tmp_path: Path) -> None:
        fixture = ReleasePipelineFixture(tmp_path)
        runner = fixture.policy_runner(changed="src/media_stack/cli/commands/release_pipeline_main.py\n")
        result = ReleaseVersionPolicyService(tmp_path, runner).check("origin/main")
        assert result.controller_changed is True
        assert "Controller/backend source changed but VERSION" in "\n".join(result.issues)

    def test_policy_passes_when_required_version_files_changed(self, tmp_path: Path) -> None:
        fixture = ReleasePipelineFixture(tmp_path)
        runner = fixture.policy_runner(
            changed="\n".join(
                [
                    "ui/src/App.tsx",
                    "VERSION-UI",
                    "ui/package.json",
                    "src/media_stack/cli/commands/release_pipeline_main.py",
                    "src/media_stack/version.py",
                    "VERSION",
                ]
            )
        )
        result = ReleaseVersionPolicyService(tmp_path, runner).check("origin/main")
        assert result.passed is True


class TestReleaseComposeDeployService:
    """Compose verification checks both service images and container IDs."""

    def test_verify_compose_passes_for_matching_images(self, tmp_path: Path) -> None:
        refs = ReleaseImageRefs("registry/controller:v1", "registry/ui:v2", "1.0.1", "1.0.2")
        runner = FakeCommandRunner(
            {
                ("docker", "inspect", "media-stack-controller", "--format", "{{.Config.Image}}"): refs.controller_image,
                ("docker", "inspect", "media-stack-ui", "--format", "{{.Config.Image}}"): refs.ui_image,
                ("docker", "inspect", "media-stack-controller", "--format", "{{.Image}}"): "sha256:controller",
                ("docker", "inspect", "media-stack-ui", "--format", "{{.Image}}"): "sha256:ui",
            }
        )
        result = ReleaseComposeDeployService(tmp_path, runner).verify(refs)
        assert result.passed is True

    def test_verify_compose_fails_for_stale_ui_image(self, tmp_path: Path) -> None:
        refs = ReleaseImageRefs("registry/controller:v1", "registry/ui:v2", "1.0.1", "1.0.2")
        runner = FakeCommandRunner(
            {
                ("docker", "inspect", "media-stack-controller", "--format", "{{.Config.Image}}"): refs.controller_image,
                ("docker", "inspect", "media-stack-ui", "--format", "{{.Config.Image}}"): "registry/ui:yesterday",
                ("docker", "inspect", "media-stack-controller", "--format", "{{.Image}}"): "sha256:controller",
                ("docker", "inspect", "media-stack-ui", "--format", "{{.Image}}"): "sha256:ui",
            }
        )
        with pytest.raises(MediaStackError, match="Compose verification failed"):
            ReleaseComposeDeployService(tmp_path, runner).verify(refs)


class TestReleaseKubernetesDeployService:
    """Kubernetes verification requires deployment specs and running pod image IDs."""

    def test_verify_kubernetes_reads_running_pod_image_ids(self) -> None:
        refs = ReleaseImageRefs("registry/controller:v1", "registry/ui:v2", "1.0.1", "1.0.2")
        runner = FakeCommandRunner(
            {
                (
                    "kubectl",
                    "-n",
                    "media-stack",
                    "get",
                    "deploy",
                    "media-stack-controller",
                    "-o",
                    'jsonpath={.spec.template.spec.containers[?(@.name=="controller")].image}',
                ): refs.controller_image,
                (
                    "kubectl",
                    "-n",
                    "media-stack",
                    "get",
                    "deploy",
                    "media-stack-ui",
                    "-o",
                    'jsonpath={.spec.template.spec.containers[?(@.name=="ui")].image}',
                ): refs.ui_image,
                (
                    "kubectl",
                    "-n",
                    "media-stack",
                    "get",
                    "pods",
                    "-l",
                    "app=media-stack-controller",
                    "-o",
                    "json",
                ): {"items": [{"spec": {"containers": [{"name": "controller", "image": refs.controller_image}]}, "status": {"phase": "Running", "containerStatuses": [{"name": "controller", "imageID": "sha256:controller"}]}}]},
                (
                    "kubectl",
                    "-n",
                    "media-stack",
                    "get",
                    "pods",
                    "-l",
                    "app=media-stack-ui",
                    "-o",
                    "json",
                ): {"items": [{"spec": {"containers": [{"name": "ui", "image": refs.ui_image}]}, "status": {"phase": "Running", "containerStatuses": [{"name": "ui", "imageID": "sha256:ui"}]}}]},
            }
        )
        result = ReleaseKubernetesDeployService(runner).verify(
            refs,
            namespace="media-stack",
            include_controller_cronjobs=False,
        )
        assert result.passed is True
        assert result.workloads[0].pod_image_ids == ("sha256:controller",)

    def test_verify_kubernetes_fails_when_ui_pods_are_not_rolled(self) -> None:
        refs = ReleaseImageRefs("registry/controller:v1", "registry/ui:v2", "1.0.1", "1.0.2")
        runner = FakeCommandRunner(
            {
                (
                    "kubectl",
                    "-n",
                    "media-stack",
                    "get",
                    "deploy",
                    "media-stack-controller",
                    "-o",
                    'jsonpath={.spec.template.spec.containers[?(@.name=="controller")].image}',
                ): refs.controller_image,
                (
                    "kubectl",
                    "-n",
                    "media-stack",
                    "get",
                    "deploy",
                    "media-stack-ui",
                    "-o",
                    'jsonpath={.spec.template.spec.containers[?(@.name=="ui")].image}',
                ): refs.ui_image,
                (
                    "kubectl",
                    "-n",
                    "media-stack",
                    "get",
                    "pods",
                    "-l",
                    "app=media-stack-controller",
                    "-o",
                    "json",
                ): {"items": [{"spec": {"containers": [{"name": "controller", "image": refs.controller_image}]}, "status": {"phase": "Running", "containerStatuses": [{"name": "controller", "imageID": "sha256:controller"}]}}]},
                (
                    "kubectl",
                    "-n",
                    "media-stack",
                    "get",
                    "pods",
                    "-l",
                    "app=media-stack-ui",
                    "-o",
                    "json",
                ): {"items": []},
            }
        )
        with pytest.raises(MediaStackError, match="Kubernetes verification failed"):
            ReleaseKubernetesDeployService(runner).verify(
                refs,
                namespace="media-stack",
                include_controller_cronjobs=False,
            )
