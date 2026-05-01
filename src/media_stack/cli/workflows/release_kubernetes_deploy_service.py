"""Kubernetes release deploy and runtime verification service."""

from __future__ import annotations

from media_stack.cli.workflows.workflow_command_runner_service import WorkflowCommandRunnerService
from media_stack.cli.workflows.workflow_interfaces import CommandRunner
from media_stack.cli.workflows.release_pipeline_models import (
    KubernetesVerificationResult,
    KubernetesWorkloadImage,
    ReleaseImageRefs,
)
from media_stack.core.cli_common import info
from media_stack.core.exceptions import MediaStackError


class ReleaseKubernetesDeployService:
    """Deploys release images and verifies actual running pod image IDs."""

    def __init__(self, command_runner: CommandRunner | None = None) -> None:
        self.command_runner = command_runner or WorkflowCommandRunnerService()

    def deploy(
        self,
        refs: ReleaseImageRefs,
        *,
        namespace: str,
        rollout_timeout: str,
        include_controller_cronjobs: bool,
    ) -> KubernetesVerificationResult:
        self.set_deployment_image(namespace, "media-stack-controller", "controller", refs.controller_image)
        self.set_deployment_image(namespace, "media-stack-ui", "ui", refs.ui_image)
        self.wait_rollout(namespace, "media-stack-controller", rollout_timeout)
        self.wait_rollout(namespace, "media-stack-ui", rollout_timeout)
        if include_controller_cronjobs:
            self.set_controller_cronjob_images(namespace, refs.controller_image)
        return self.verify(
            refs,
            namespace=namespace,
            include_controller_cronjobs=include_controller_cronjobs,
        )

    def verify(
        self,
        refs: ReleaseImageRefs,
        *,
        namespace: str,
        include_controller_cronjobs: bool,
    ) -> KubernetesVerificationResult:
        workloads: list[KubernetesWorkloadImage] = [
            self.deployment_state(
                namespace,
                "media-stack-controller",
                "controller",
                refs.controller_image,
                "app=media-stack-controller",
            ),
            self.deployment_state(namespace, "media-stack-ui", "ui", refs.ui_image, "app=media-stack-ui"),
        ]
        if include_controller_cronjobs:
            workloads.extend(self.controller_cronjob_states(namespace, refs.controller_image))
        result = KubernetesVerificationResult(namespace=namespace, workloads=tuple(workloads))
        if not result.passed:
            raise MediaStackError("Kubernetes verification failed: specs or running pod image IDs are not ready.")
        return result

    def set_deployment_image(self, namespace: str, deployment: str, container: str, image: str) -> None:
        info(f"Setting deployment/{deployment} container {container} to {image}")
        self.command_runner.run_text(
            ["kubectl", "-n", namespace, "set", "image", f"deployment/{deployment}", f"{container}={image}"]
        )

    def wait_rollout(self, namespace: str, deployment: str, timeout: str) -> None:
        self.command_runner.run_text(
            ["kubectl", "-n", namespace, "rollout", "status", f"deployment/{deployment}", f"--timeout={timeout}"]
        )

    def set_controller_cronjob_images(self, namespace: str, image: str) -> None:
        for cronjob, container in self.controller_cronjob_containers(namespace):
            info(f"Setting cronjob/{cronjob} container {container} to {image}")
            self.command_runner.run_text(
                ["kubectl", "-n", namespace, "set", "image", f"cronjob/{cronjob}", f"{container}={image}"]
            )

    def deployment_state(
        self,
        namespace: str,
        deployment: str,
        container: str,
        expected_image: str,
        pod_selector: str,
    ) -> KubernetesWorkloadImage:
        return KubernetesWorkloadImage(
            workload=f"deployment/{deployment}",
            container=container,
            expected_image=expected_image,
            spec_image=self.deployment_spec_image(namespace, deployment, container),
            pod_image_ids=tuple(
                self.pod_image_ids(namespace, pod_selector, container, expected_image)
            ),
        )

    def deployment_spec_image(self, namespace: str, deployment: str, container: str) -> str:
        path = "{.spec.template.spec.containers[?(@.name==\"" + container + "\")].image}"
        return self.command_runner.run_text(
            ["kubectl", "-n", namespace, "get", "deploy", deployment, "-o", f"jsonpath={path}"]
        )

    def pod_image_ids(
        self,
        namespace: str,
        selector: str,
        container: str,
        expected_image: str,
    ) -> list[str]:
        data = self.command_runner.run_json(
            ["kubectl", "-n", namespace, "get", "pods", "-l", selector, "-o", "json"]
        )
        items = data.get("items", []) if isinstance(data, dict) else []
        image_ids: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata", {})
            if isinstance(metadata, dict) and metadata.get("deletionTimestamp"):
                continue
            if item.get("status", {}).get("phase") not in {"Running", "Succeeded"}:
                continue
            spec_containers = item.get("spec", {}).get("containers", [])
            spec_image = self.pod_container_spec_image(spec_containers, container)
            if spec_image and spec_image != expected_image:
                return []
            statuses = item.get("status", {}).get("containerStatuses", []) if isinstance(item, dict) else []
            for status in statuses:
                if isinstance(status, dict) and status.get("name") == container:
                    image_id = str(status.get("imageID", "")).strip()
                    if image_id:
                        image_ids.append(image_id)
        return image_ids

    def pod_container_spec_image(self, containers: object, container: str) -> str:
        if not isinstance(containers, list):
            return ""
        for item in containers:
            if isinstance(item, dict) and item.get("name") == container:
                return str(item.get("image", "")).strip()
        return ""

    def controller_cronjob_states(self, namespace: str, expected_image: str) -> list[KubernetesWorkloadImage]:
        states: list[KubernetesWorkloadImage] = []
        for cronjob, container in self.controller_cronjob_containers(namespace):
            states.append(
                KubernetesWorkloadImage(
                    workload=f"cronjob/{cronjob}",
                    container=container,
                    expected_image=expected_image,
                    spec_image=self.cronjob_container_image(namespace, cronjob, container),
                    pod_image_ids=("template-only",),
                )
            )
        return states

    def controller_cronjob_containers(self, namespace: str) -> list[tuple[str, str]]:
        data = self.command_runner.run_json(["kubectl", "-n", namespace, "get", "cronjob", "-o", "json"])
        items = data.get("items", []) if isinstance(data, dict) else []
        out: list[tuple[str, str]] = []
        for item in items:
            metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
            name = str(metadata.get("name", "")).strip()
            template = (
                item.get("spec", {})
                .get("jobTemplate", {})
                .get("spec", {})
                .get("template", {})
                .get("spec", {})
                if isinstance(item, dict)
                else {}
            )
            containers = template.get("containers", []) if isinstance(template, dict) else []
            for container in containers:
                image = str(container.get("image", "")) if isinstance(container, dict) else ""
                container_name = str(container.get("name", "")) if isinstance(container, dict) else ""
                if name and container_name and "media-stack-controller" in image:
                    out.append((name, container_name))
        return out

    def cronjob_container_image(self, namespace: str, cronjob: str, container: str) -> str:
        path = (
            "{.spec.jobTemplate.spec.template.spec.containers[?(@.name==\""
            + container
            + "\")].image}"
        )
        return self.command_runner.run_text(
            ["kubectl", "-n", namespace, "get", "cronjob", cronjob, "-o", f"jsonpath={path}"]
        )
