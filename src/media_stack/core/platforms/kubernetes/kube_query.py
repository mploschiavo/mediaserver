"""Query operations mixin for the Kubernetes client adapter.

Handles get, describe, and logs commands. Mixed into KubernetesClient
by kube_client.py.
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import json
from typing import Any

import yaml

from media_stack.core.exceptions import ConfigError
from media_stack.core.platforms.kubernetes.kube_helpers import (
    _format_api_error,
    _parse_jsonpath_key,
    _render_custom_columns,
)
from media_stack.core.subprocess_utils import CommandResult
import logging


class QueryMixin:
    """Mixin providing get/describe/logs operations for KubernetesClient."""

    # These attributes are provided by the base KubernetesClient class.
    _core_v1: Any
    _apps_v1: Any
    _batch_v1: Any
    _networking_v1: Any

    def _result(self, args: list[str], **kwargs: Any) -> CommandResult:  # type: ignore[empty-body]
        ...

    def _error_result(self, args: list[str], message: str, **kwargs: Any) -> CommandResult:  # type: ignore[empty-body]
        ...

    def _parse_get_flags(self, remainder: list[str]) -> tuple[str, str, str, bool]:
        selector = ""
        output = ""
        name = ""
        no_headers = False
        cursor = 0
        while cursor < len(remainder):
            token = remainder[cursor]
            if token == "-l" and cursor + 1 < len(remainder):
                selector = str(remainder[cursor + 1] or "")
                cursor += 2
                continue
            if token == "-o" and cursor + 1 < len(remainder):
                output = str(remainder[cursor + 1] or "")
                cursor += 2
                continue
            if token.startswith("-o="):
                output = token.split("=", 1)[1]
                cursor += 1
                continue
            if token == "--no-headers":
                no_headers = True
                cursor += 1
                continue
            if not token.startswith("-") and not name:
                name = token
                cursor += 1
                continue
            cursor += 1
        return selector, output, name, no_headers

    def _run_get(self, args: list[str], namespace: str, remainder: list[str]) -> CommandResult:
        if not remainder:
            return self._error_result(args, "Missing get resource target.", returncode=2)

        target = remainder[0]
        if "/" in target:
            resource_type, resource_name = target.split("/", 1)
            selector, output, explicit_name, no_headers = self._parse_get_flags(remainder[1:])
            name = explicit_name or resource_name
        else:
            resource_type = target
            selector, output, name, no_headers = self._parse_get_flags(remainder[1:])

        kind = str(resource_type or "").strip().lower()
        try:
            if kind in ("secret", "secrets"):
                if not name:
                    payload = self._core_v1.list_namespaced_secret(
                        namespace=namespace, label_selector=selector
                    )
                    items = [item.to_dict() for item in payload.items or []]
                    return self._render_get_result(args, items, output, no_headers=no_headers)
                obj = self._core_v1.read_namespaced_secret(name=name, namespace=namespace)
                return self._render_get_result(args, obj.to_dict(), output, no_headers=no_headers)

            if kind in ("pvc", "persistentvolumeclaim", "persistentvolumeclaims"):
                if not name:
                    payload = self._core_v1.list_namespaced_persistent_volume_claim(
                        namespace=namespace, label_selector=selector
                    )
                    items = [item.to_dict() for item in payload.items or []]
                    return self._render_get_result(args, items, output, no_headers=no_headers)
                obj = self._core_v1.read_namespaced_persistent_volume_claim(
                    name=name, namespace=namespace
                )
                return self._render_get_result(args, obj.to_dict(), output, no_headers=no_headers)

            if kind in ("job", "jobs"):
                if not name:
                    payload = self._batch_v1.list_namespaced_job(
                        namespace=namespace, label_selector=selector
                    )
                    items = [item.to_dict() for item in payload.items or []]
                    return self._render_get_result(args, items, output, no_headers=no_headers)
                obj = self._batch_v1.read_namespaced_job(name=name, namespace=namespace)
                return self._render_get_result(args, obj.to_dict(), output, no_headers=no_headers)

            if kind in ("pod", "pods"):
                if not name:
                    payload = self._core_v1.list_namespaced_pod(
                        namespace=namespace, label_selector=selector
                    )
                    items = [item.to_dict() for item in payload.items or []]
                    return self._render_get_result(args, items, output, no_headers=no_headers)
                obj = self._core_v1.read_namespaced_pod(name=name, namespace=namespace)
                return self._render_get_result(args, obj.to_dict(), output, no_headers=no_headers)

            if kind in ("deploy", "deployment", "deployments"):
                if not name:
                    payload = self._apps_v1.list_namespaced_deployment(
                        namespace=namespace, label_selector=selector
                    )
                    items = [item.to_dict() for item in payload.items or []]
                    return self._render_get_result(args, items, output, no_headers=no_headers)
                obj = self._apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
                return self._render_get_result(args, obj.to_dict(), output, no_headers=no_headers)
            if kind in ("namespace", "namespaces", "ns"):
                if not name:
                    payload = self._core_v1.list_namespace(label_selector=selector)
                    items = [item.to_dict() for item in payload.items or []]
                    return self._render_get_result(args, items, output, no_headers=no_headers)
                obj = self._core_v1.read_namespace(name=name)
                return self._render_get_result(args, obj.to_dict(), output, no_headers=no_headers)
            if kind in ("ingress", "ingresses"):
                if not name:
                    payload = self._networking_v1.list_namespaced_ingress(
                        namespace=namespace, label_selector=selector
                    )
                    items = [item.to_dict() for item in payload.items or []]
                    return self._render_get_result(args, items, output, no_headers=no_headers)
                obj = self._networking_v1.read_namespaced_ingress(name=name, namespace=namespace)
                return self._render_get_result(args, obj.to_dict(), output, no_headers=no_headers)
            if kind in ("ingressclass", "ingressclasses"):
                if not name:
                    payload = self._networking_v1.list_ingress_class()
                    items = [item.to_dict() for item in payload.items or []]
                    return self._render_get_result(args, items, output, no_headers=no_headers)
                obj = self._networking_v1.read_ingress_class(name=name)
                return self._render_get_result(args, obj.to_dict(), output, no_headers=no_headers)
        except Exception as exc:
            status, message = _format_api_error(exc)
            return self._error_result(args, message, returncode=status or 1)

        return self._error_result(
            args, f"Unsupported get resource type: {resource_type}", returncode=2
        )

    def _render_get_result(
        self,
        args: list[str],
        payload: dict[str, Any] | list[dict[str, Any]],
        output: str,
        *,
        no_headers: bool,
    ) -> CommandResult:
        output = str(output or "").strip()
        is_list = isinstance(payload, list)
        rows = payload if is_list else [payload]

        if output == "json":
            return self._result(args, stdout=json.dumps(payload, default=str))

        if output.startswith("jsonpath"):
            if is_list:
                return self._result(args, stdout="")
            key_name = _parse_jsonpath_key(output)
            if not key_name:
                return self._result(args, stdout="")
            data = (payload.get("data") or {}) if isinstance(payload, dict) else {}
            value = str((data or {}).get(key_name) or "")
            return self._result(args, stdout=value)

        if output.startswith("custom-columns="):
            columns = output.split("=", 1)[1]
            table = _render_custom_columns(rows, columns, no_headers=no_headers)
            return self._result(args, stdout=table)

        if output == "wide":
            if not rows:
                return self._result(args, stdout="")
            header = "NAME PHASE POD_IP NODE\n"
            lines = []
            for item in rows:
                meta = item.get("metadata") or {}
                status = item.get("status") or {}
                spec = item.get("spec") or {}
                lines.append(
                    f"{meta.get('name','')} {status.get('phase','')} "
                    f"{status.get('pod_ip','')} {spec.get('node_name','')}"
                )
            return self._result(args, stdout=header + "\n".join(lines).rstrip() + "\n")

        if is_list:
            names = []
            for item in rows:
                meta = item.get("metadata") or {}
                names.append(str(meta.get("name") or ""))
            text = "\n".join(name for name in names if name).rstrip()
            return self._result(args, stdout=(text + "\n") if text else "")

        name = str(((payload or {}).get("metadata") or {}).get("name") or "")
        return self._result(args, stdout=(name + "\n") if name else "")

    def _describe_object(self, kind: str, namespace: str, name: str) -> dict[str, Any]:
        token = str(kind or "").strip().lower()
        if token in ("pod", "pods"):
            return self._core_v1.read_namespaced_pod(name=name, namespace=namespace).to_dict()
        if token in ("job", "jobs"):
            return self._batch_v1.read_namespaced_job(name=name, namespace=namespace).to_dict()
        if token in ("deploy", "deployment", "deployments"):
            return self._apps_v1.read_namespaced_deployment(
                name=name, namespace=namespace
            ).to_dict()
        if token in ("secret", "secrets"):
            return self._core_v1.read_namespaced_secret(name=name, namespace=namespace).to_dict()
        if token in ("pvc", "persistentvolumeclaim", "persistentvolumeclaims"):
            return self._core_v1.read_namespaced_persistent_volume_claim(
                name=name, namespace=namespace
            ).to_dict()
        raise ConfigError(f"Unsupported describe kind: {kind}")

    def _run_describe(self, args: list[str], namespace: str, remainder: list[str]) -> CommandResult:
        if len(remainder) < 2:
            return self._error_result(args, "describe requires kind and name.", returncode=2)
        kind = remainder[0]
        name = remainder[1]
        try:
            payload = self._describe_object(kind, namespace, name)
        except Exception as exc:
            status, message = _format_api_error(exc)
            return self._error_result(args, message, returncode=status or 1)
        return self._result(args, stdout=yaml.safe_dump(payload, sort_keys=False))

    def _first_job_pod(self, namespace: str, job_name: str) -> str:
        pods = (
            self._core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}",
            ).items
            or []
        )
        if not pods:
            return ""
        # Prefer running pod if present.
        running = [item for item in pods if str((item.status or {}).phase or "") == "Running"]
        selected = running[0] if running else pods[0]
        return str((selected.metadata or {}).name or "")

    def _run_logs(self, args: list[str], namespace: str, remainder: list[str]) -> CommandResult:
        if not remainder:
            return self._error_result(args, "logs requires pod/job target.", returncode=2)

        target = ""
        label_selector = ""
        tail_lines = None
        timestamps = False
        idx = 0
        while idx < len(remainder):
            token = str(remainder[idx] or "").strip()
            if token.startswith("--tail="):
                try:
                    tail_lines = int(token.split("=", 1)[1])
                except Exception:
                    tail_lines = None
            elif token == "--timestamps":
                timestamps = True
            elif token == "-l" and idx + 1 < len(remainder):
                idx += 1
                label_selector = str(remainder[idx] or "").strip()
            elif not target:
                target = token
            idx += 1

        # Label selector mode: find first pod matching label.
        if label_selector and not target:
            pods = []
            try:
                payload = self._core_v1.list_namespaced_pod(
                    namespace=namespace, label_selector=label_selector
                )
                pods = [p for p in (payload.items or []) if p.status and p.status.phase == "Running"]
            except Exception as exc:
                log_swallowed(exc)
            if not pods:
                return self._error_result(
                    args, f"No running pods found for selector '{label_selector}'", returncode=1
                )
            target = pods[0].metadata.name

        if not target:
            return self._error_result(args, "logs requires pod/job target.", returncode=2)

        if target.startswith("job/"):
            pod_name = self._first_job_pod(namespace, target.split("/", 1)[1])
            if not pod_name:
                return self._error_result(args, f"No pods found for {target}", returncode=1)
        else:
            pod_name = target

        try:
            data = self._core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                tail_lines=tail_lines,
                timestamps=timestamps,
            )
        except Exception as exc:
            status, message = _format_api_error(exc)
            return self._error_result(args, message, returncode=status or 1)

        text = str(data or "")
        if text and not text.endswith("\n"):
            text += "\n"
        return self._result(args, stdout=text)
