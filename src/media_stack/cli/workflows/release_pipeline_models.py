"""Shared models for release build, deploy, and verification workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ReleaseImageRefs:
    """Immutable image references selected for a release."""

    controller_image: str
    ui_image: str
    controller_version: str
    ui_version: str


@dataclass(frozen=True)
class ReleaseBuildResult:
    """Image metadata emitted after build/push."""

    controller_image: str
    controller_digest: str
    ui_image: str
    ui_digest: str
    controller_version: str
    ui_version: str


@dataclass(frozen=True)
class ReleasePolicyResult:
    """Structured result for version-policy checks."""

    base_ref: str
    ui_changed: bool
    controller_changed: bool
    base_versions: Mapping[str, str]
    current_versions: Mapping[str, str]
    changed_files: Sequence[str] = field(default_factory=tuple)
    issues: Sequence[str] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class ComposeVerificationResult:
    """Expected and observed Compose runtime image state."""

    expected_controller_image: str
    expected_ui_image: str
    running_controller_image: str
    running_ui_image: str
    controller_image_id: str
    ui_image_id: str

    @property
    def passed(self) -> bool:
        return (
            self.expected_controller_image == self.running_controller_image
            and self.expected_ui_image == self.running_ui_image
            and bool(self.controller_image_id)
            and bool(self.ui_image_id)
        )


@dataclass(frozen=True)
class KubernetesWorkloadImage:
    """Image state for one Kubernetes workload/container."""

    workload: str
    container: str
    expected_image: str
    spec_image: str
    pod_image_ids: Sequence[str] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return self.expected_image == self.spec_image and bool(self.pod_image_ids)


@dataclass(frozen=True)
class KubernetesVerificationResult:
    """Expected and observed Kubernetes release state."""

    namespace: str
    workloads: Sequence[KubernetesWorkloadImage]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.workloads)


@dataclass(frozen=True)
class ReleasePipelineConfig:
    """CLI-selected release settings."""

    root_dir: Path
    base_ref: str = "origin/main"
    namespace: str = "media-stack"
    controller_image: str = ""
    ui_image: str = ""
    output_json: str = ""
    no_push: bool = False
    skip_policy_check: bool = False
    include_controller_cronjobs: bool = False
    rollout_timeout: str = "300s"
    controller_health_url: str = "http://127.0.0.1:9100/healthz"
    ui_health_url: str = "http://127.0.0.1:9101/healthz"
    health_timeout_seconds: int = 180
