"""Kubernetes teardown planning strategy."""

from __future__ import annotations

import shutil

from media_stack.cli.workflows.teardown_models import TeardownAction, TeardownRequest
from media_stack.cli.workflows.teardown_safety_policy_service import TeardownSafetyPolicyService


class TeardownKubernetesStrategy:
    """Plans Kubernetes namespace teardown actions."""

    def __init__(self, safety_policy: TeardownSafetyPolicyService | None = None) -> None:
        self.safety_policy = safety_policy or TeardownSafetyPolicyService()

    def plan(self, request: TeardownRequest) -> tuple[TeardownAction, ...]:
        if not self.has_kubectl():
            return (
                TeardownAction(
                    kind="refuse",
                    description="kubectl is not on PATH — skipping k8s teardown",
                ),
            )
        return (self.safety_policy.namespace_delete_action(request),)

    def has_kubectl(self) -> bool:
        return shutil.which("kubectl") is not None
