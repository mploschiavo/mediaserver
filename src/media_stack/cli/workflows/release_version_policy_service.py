"""Version bump policy for release candidates."""

from __future__ import annotations

import json
from pathlib import Path

from media_stack.cli.workflows.workflow_command_runner_service import WorkflowCommandRunnerService
from media_stack.cli.workflows.workflow_interfaces import CommandRunner
from media_stack.cli.workflows.release_pipeline_config_service import ReleasePipelineConfigService
from media_stack.cli.workflows.release_pipeline_models import ReleasePolicyResult


class ReleaseVersionPolicyService:
    """Ensures changed code cannot reuse an old release tag."""

    def __init__(
        self,
        root_dir: Path,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.command_runner = command_runner or WorkflowCommandRunnerService()
        self.config = ReleasePipelineConfigService(root_dir)

    def check(self, base_ref: str) -> ReleasePolicyResult:
        changed_files = tuple(self.changed_files(base_ref))
        changed_set = set(changed_files)
        ui_changed = self.ui_changed(changed_files)
        controller_changed = self.controller_changed(changed_files)
        base_ui_version = self.git_file_at_ref(base_ref, "VERSION-UI").strip()
        base_controller_version = self.git_file_at_ref(base_ref, "VERSION").strip()
        base_package = json.loads(self.git_file_at_ref(base_ref, "ui/package.json"))
        current_ui_version = self.config.ui_version()
        current_controller_version = self.config.controller_version()
        current_package_version = self.config.ui_package_version()
        issues = []

        if current_package_version != current_ui_version:
            issues.append(
                "UI version mismatch: VERSION-UI="
                f"{current_ui_version} but ui/package.json={current_package_version}"
            )
        if ui_changed:
            if "VERSION-UI" not in changed_set or "ui/package.json" not in changed_set:
                issues.append("UI source changed but VERSION-UI and ui/package.json were not both updated.")
            if not self.version_gt(current_ui_version, base_ui_version):
                issues.append(
                    "UI source changed but VERSION-UI did not increase "
                    f"({base_ui_version} -> {current_ui_version})."
                )
            if not self.version_gt(current_package_version, str(base_package.get("version", ""))):
                issues.append("UI source changed but ui/package.json version did not increase.")
        if controller_changed:
            if "VERSION" not in changed_set or "src/media_stack/version.py" not in changed_set:
                issues.append(
                    "Controller/backend source changed but VERSION and src/media_stack/version.py "
                    "were not both updated."
                )
            if not self.version_gt(current_controller_version, base_controller_version):
                issues.append(
                    "Controller/backend source changed but VERSION did not increase "
                    f"({base_controller_version} -> {current_controller_version})."
                )

        return ReleasePolicyResult(
            base_ref=base_ref,
            ui_changed=ui_changed,
            controller_changed=controller_changed,
            base_versions={
                "controller": base_controller_version,
                "ui": base_ui_version,
                "ui_package": str(base_package.get("version", "")),
            },
            current_versions={
                "controller": current_controller_version,
                "ui": current_ui_version,
                "ui_package": current_package_version,
            },
            changed_files=changed_files,
            issues=tuple(issues),
        )

    def changed_files(self, base_ref: str) -> list[str]:
        committed = self.command_runner.run_text(["git", "diff", "--name-only", f"{base_ref}...HEAD"])
        unstaged = self.command_runner.run_text(["git", "diff", "--name-only"])
        staged = self.command_runner.run_text(["git", "diff", "--cached", "--name-only"])
        untracked = self.command_runner.run_text(["git", "ls-files", "--others", "--exclude-standard"])
        combined = "\n".join(part for part in (committed, unstaged, staged, untracked) if part)
        return sorted({line.strip() for line in combined.splitlines() if line.strip()})

    def git_file_at_ref(self, ref: str, path: str) -> str:
        return self.command_runner.run_text(["git", "show", f"{ref}:{path}"])

    def ui_changed(self, paths: tuple[str, ...] | list[str]) -> bool:
        prefixes = ("ui/src/", "ui/public/", "ui/index.html", "ui/package.json")
        return any(path.startswith(prefixes) or path == "VERSION-UI" for path in paths)

    def controller_changed(self, paths: tuple[str, ...] | list[str]) -> bool:
        prefixes = ("src/media_stack/", "deploy/compose/controller.Dockerfile", "pyproject.toml")
        return any(path.startswith(prefixes) or path == "VERSION" for path in paths)

    def version_gt(self, current: str, base: str) -> bool:
        return self.version_triplet(current) > self.version_triplet(base)

    def version_triplet(self, version: str) -> tuple[int, int, int]:
        parts = version.strip().lstrip("v").split(".")
        if len(parts) != 3:
            raise ValueError(f"Version must be X.Y.Z, got '{version}'")
        return int(parts[0]), int(parts[1]), int(parts[2])
