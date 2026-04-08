"""Workload operations mixin for the Kubernetes client adapter.

Handles rollout, scale, patch, delete, and exec commands. Mixed into
KubernetesClient by kube_client.py.
"""

from __future__ import annotations

import json
import time
from typing import Any

from media_stack.core.exceptions import KubernetesError
from media_stack.core.platforms.kubernetes.kube_helpers import (
    _format_api_error,
    _parse_timeout_seconds,
    _selector_from_match_labels,
)
from media_stack.core.subprocess_utils import CommandResult


class WorkloadsMixin:
    """Mixin providing rollout/scale/patch/delete/exec operations for KubernetesClient."""

    # These attributes are provided by the base KubernetesClient class.
    _core_v1: Any
    _apps_v1: Any
    _batch_v1: Any
    _networking_v1: Any
    _k8s_stream: Any

    def _result(self, args: list[str], **kwargs: Any) -> CommandResult:  # type: ignore[empty-body]
        ...

    def _error_result(self, args: list[str], message: str, **kwargs: Any) -> CommandResult:  # type: ignore[empty-body]
        ...

    def _run_rollout(
        self,
        args: list[str],
        namespace: str,
        remainder: list[str],
        *,
        timeout: int | None,
    ) -> CommandResult:
        if len(remainder) < 2:
            return self._error_result(
                args, "rollout requires a subcommand and target.", returncode=2
            )
        sub = remainder[0]
        target = remainder[1]
        if "/" in target:
            _, name = target.split("/", 1)
        else:
            name = target

        if sub == "restart":
            body = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": time.strftime(
                                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                )
                            }
                        }
                    }
                }
            }
            try:
                self._apps_v1.patch_namespaced_deployment(name=name, namespace=namespace, body=body)
            except Exception as exc:
                status, message = _format_api_error(exc)
                return self._error_result(args, message, returncode=status or 1)
            return self._result(args, stdout=f"deployment.apps/{name} restarted\n")

        if sub == "status":
            timeout_seconds = timeout or 300
            for token in remainder[2:]:
                if token.startswith("--timeout="):
                    timeout_seconds = _parse_timeout_seconds(
                        token.split("=", 1)[1], default=timeout_seconds
                    )
            deadline = time.time() + timeout_seconds
            while time.time() <= deadline:
                try:
                    dep = self._apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
                except Exception as exc:
                    status, message = _format_api_error(exc)
                    return self._error_result(args, message, returncode=status or 1)
                spec_replicas = int((dep.spec or {}).replicas or 0)
                status_obj = dep.status or {}
                updated = int(getattr(status_obj, "updated_replicas", 0) or 0)
                ready = int(getattr(status_obj, "ready_replicas", 0) or 0)
                available = int(getattr(status_obj, "available_replicas", 0) or 0)
                observed = int(getattr(status_obj, "observed_generation", 0) or 0)
                generation = int((dep.metadata or {}).generation or 0)
                if (
                    observed >= generation
                    and updated >= spec_replicas
                    and ready >= spec_replicas
                    and available >= spec_replicas
                ):
                    return self._result(args, stdout=f"deployment/{name} successfully rolled out\n")
                time.sleep(2)
            return self._error_result(
                args,
                f"Timed out waiting for deployment/{name} rollout status.",
                returncode=1,
            )

        return self._error_result(args, f"Unsupported rollout subcommand: {sub}", returncode=2)

    def _run_patch(self, args: list[str], namespace: str, remainder: list[str]) -> CommandResult:
        if len(remainder) < 3:
            return self._error_result(
                args, "patch requires resource, name, and payload.", returncode=2
            )
        resource = remainder[0]
        name = remainder[1]
        payload = ""
        cursor = 2
        while cursor < len(remainder):
            token = remainder[cursor]
            if token == "-p" and cursor + 1 < len(remainder):
                payload = remainder[cursor + 1]
                cursor += 2
                continue
            if token.startswith("-p="):
                payload = token.split("=", 1)[1]
                cursor += 1
                continue
            cursor += 1
        if not payload:
            return self._error_result(args, "patch payload missing (-p).", returncode=2)

        try:
            body = json.loads(payload)
        except Exception as exc:
            return self._error_result(args, f"Invalid patch payload: {exc}", returncode=2)

        if resource not in ("secret", "secrets"):
            if resource not in ("ingress", "ingresses"):
                return self._error_result(
                    args,
                    f"Unsupported patch resource: {resource}",
                    returncode=2,
                )

        try:
            if resource in ("secret", "secrets"):
                self._core_v1.patch_namespaced_secret(name=name, namespace=namespace, body=body)
            else:
                self._networking_v1.patch_namespaced_ingress(
                    name=name, namespace=namespace, body=body
                )
        except Exception as exc:
            status, message = _format_api_error(exc)
            return self._error_result(args, message, returncode=status or 1)
        if resource in ("secret", "secrets"):
            return self._result(args, stdout=f"secret/{name} patched\n")
        return self._result(args, stdout=f"ingress.networking.k8s.io/{name} patched\n")

    def _run_delete(self, args: list[str], namespace: str, remainder: list[str]) -> CommandResult:
        if len(remainder) < 2:
            return self._error_result(args, "delete requires resource and name.", returncode=2)
        resource = remainder[0]
        name = remainder[1]
        ignore_not_found = "--ignore-not-found" in remainder
        try:
            if resource in ("job", "jobs"):
                self._batch_v1.delete_namespaced_job(
                    name=name,
                    namespace=namespace,
                    propagation_policy="Background",
                )
            elif resource in ("namespace", "namespaces", "ns"):
                self._core_v1.delete_namespace(name=name)
            else:
                return self._error_result(
                    args,
                    f"Unsupported delete resource: {resource}",
                    returncode=2,
                )
        except Exception as exc:
            status, message = _format_api_error(exc)
            if ignore_not_found and status == 404:
                return self._result(args, stdout="")
            return self._error_result(args, message, returncode=status or 1)
        if resource in ("job", "jobs"):
            return self._result(args, stdout=f"job.batch/{name} deleted\n")
        return self._result(args, stdout=f"namespace/{name} deleted\n")

    def _run_scale(self, args: list[str], namespace: str, remainder: list[str]) -> CommandResult:
        if not remainder:
            return self._error_result(args, "scale requires target.", returncode=2)
        target = str(remainder[0] or "").strip()
        resource = ""
        name = ""
        cursor = 1
        if "/" in target:
            resource, name = target.split("/", 1)
        else:
            resource = target
            if cursor < len(remainder):
                name = str(remainder[cursor] or "").strip()
                cursor += 1

        replicas_value = ""
        while cursor < len(remainder):
            token = str(remainder[cursor] or "").strip()
            if token.startswith("--replicas="):
                replicas_value = token.split("=", 1)[1]
                cursor += 1
                continue
            if token == "--replicas" and cursor + 1 < len(remainder):
                replicas_value = str(remainder[cursor + 1] or "").strip()
                cursor += 2
                continue
            cursor += 1

        if resource not in ("deploy", "deployment", "deployments"):
            return self._error_result(args, f"Unsupported scale resource: {resource}", returncode=2)
        if not name:
            return self._error_result(args, "scale requires deployment name.", returncode=2)
        try:
            replicas = int(replicas_value)
        except Exception:
            return self._error_result(args, "scale requires valid --replicas value.", returncode=2)

        body = {"spec": {"replicas": replicas}}
        try:
            self._apps_v1.patch_namespaced_deployment(name=name, namespace=namespace, body=body)
        except Exception as exc:
            status, message = _format_api_error(exc)
            return self._error_result(args, message, returncode=status or 1)
        return self._result(args, stdout=f"deployment.apps/{name} scaled\n")

    def _pod_from_deployment(self, namespace: str, deployment_name: str) -> str:
        dep = self._apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        selector = _selector_from_match_labels((dep.spec or {}).selector.match_labels)
        pods = (
            self._core_v1.list_namespaced_pod(namespace=namespace, label_selector=selector).items
            or []
        )
        if not pods:
            raise KubernetesError(f"No pods found for deployment/{deployment_name}")
        running = [pod for pod in pods if str((pod.status or {}).phase or "") == "Running"]
        selected = running[0] if running else pods[0]
        return str((selected.metadata or {}).name or "")

    def _exec_pod(self, namespace: str, pod_name: str, command: list[str]) -> tuple[int, str, str]:
        ws = self._k8s_stream.stream(
            self._core_v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        stdout = ""
        stderr = ""
        while ws.is_open():
            ws.update(timeout=1)
            if ws.peek_stdout():
                stdout += ws.read_stdout()
            if ws.peek_stderr():
                stderr += ws.read_stderr()
            if ws.returncode is not None:
                break
        ws.close()
        return int(ws.returncode or 0), stdout, stderr

    def _run_exec(self, args: list[str], namespace: str, remainder: list[str]) -> CommandResult:
        if not remainder:
            return self._error_result(args, "exec requires target and command.", returncode=2)
        target = remainder[0]
        if "--" not in remainder:
            return self._error_result(args, "exec requires '--' before command.", returncode=2)
        split_idx = remainder.index("--")
        command = remainder[split_idx + 1 :]
        if not command:
            return self._error_result(args, "exec command missing after '--'.", returncode=2)

        try:
            if target.startswith("deploy/") or target.startswith("deployment/"):
                deployment_name = target.split("/", 1)[1]
                pod_name = self._pod_from_deployment(namespace, deployment_name)
            elif target.startswith("pod/"):
                pod_name = target.split("/", 1)[1]
            else:
                pod_name = target
            rc, stdout, stderr = self._exec_pod(namespace, pod_name, command)
        except Exception as exc:
            status, message = _format_api_error(exc)
            return self._error_result(args, message, returncode=status or 1)

        return self._result(args, returncode=rc, stdout=stdout, stderr=stderr)
