"""Kubernetes API adapter.

This module keeps the historical `KubernetesClient.run([...])` interface used by
CLI services, but executes against the official Kubernetes Python client
(`kubernetes-client/python`) instead of shelling out to kubectl.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from media_stack.core.decorators import retry, timed
from media_stack.core.exceptions import ConfigError, KubernetesError
from media_stack.core.subprocess_utils import CommandExecutionError, CommandResult, CommandRunner

KUBECTL_RETRY_ATTEMPTS = max(1, int(os.environ.get("MEDIA_STACK_KUBECTL_RETRY_ATTEMPTS", "3")))
KUBECTL_RETRY_DELAY_SECONDS = float(
    os.environ.get("MEDIA_STACK_KUBECTL_RETRY_DELAY_SECONDS", "0.5")
)
KUBECTL_RETRY_MAX_DELAY_SECONDS = float(
    os.environ.get("MEDIA_STACK_KUBECTL_RETRY_MAX_DELAY_SECONDS", "3")
)
KUBECTL_RETRY_BACKOFF = float(os.environ.get("MEDIA_STACK_KUBECTL_RETRY_BACKOFF", "2"))


def _env_truthy(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _is_retryable_kubectl_error(exc: Exception) -> bool:
    text = str(exc).lower()
    retryable_markers = (
        "i/o timeout",
        "timed out",
        "connection refused",
        "connection reset by peer",
        "tls handshake timeout",
        "context deadline exceeded",
        "service unavailable",
        "temporarily unavailable",
        "unable to connect to the server",
        "net/http: request canceled",
    )
    return any(marker in text for marker in retryable_markers)


def resolve_kubectl_binary() -> list[str]:
    if shutil.which("microk8s"):
        return ["microk8s", "kubectl"]
    if shutil.which("kubectl"):
        return ["kubectl"]
    raise ConfigError("Neither microk8s nor kubectl is available in PATH.")


def _format_api_error(exc: Exception) -> tuple[int, str]:
    status = int(getattr(exc, "status", 1) or 1)
    body = str(getattr(exc, "body", "") or "").strip()
    reason = str(getattr(exc, "reason", "") or "").strip()
    message = body or reason or str(exc)
    return status, message


def _selector_from_match_labels(labels: dict[str, str] | None) -> str:
    if not isinstance(labels, dict) or not labels:
        return ""
    parts = [f"{k}={v}" for k, v in labels.items() if str(k).strip() and str(v).strip()]
    return ",".join(parts)


def _parse_timeout_seconds(value: str | None, default: int = 60) -> int:
    token = str(value or "").strip().lower()
    if not token:
        return default
    if token.endswith("s"):
        token = token[:-1]
    try:
        return max(1, int(token))
    except Exception:
        return default


def _parse_jsonpath_key(expr: str) -> str:
    token = str(expr or "").strip()
    if token.startswith("jsonpath="):
        token = token[len("jsonpath=") :]
    if token.startswith("{") and token.endswith("}"):
        token = token[1:-1]
    marker = ".data."
    idx = token.find(marker)
    if idx < 0:
        return ""
    return token[idx + len(marker) :].strip()


def _extract_path_value(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    path = str(dotted_path or "").lstrip(".")
    if not path:
        return ""
    for token in path.split("."):
        token = token.strip()
        if not token:
            continue
        index = None
        if "[" in token and token.endswith("]"):
            name, _, tail = token.partition("[")
            token = name
            raw_idx = tail[:-1]
            try:
                index = int(raw_idx)
            except Exception:
                index = None
        if token:
            if not isinstance(current, dict):
                return ""
            current = current.get(token)
        if index is not None:
            if not isinstance(current, list) or index >= len(current):
                return ""
            current = current[index]
        if current is None:
            return ""
    return current


def _render_custom_columns(
    rows: list[dict[str, Any]],
    spec: str,
    *,
    no_headers: bool,
) -> str:
    parts = [item.strip() for item in str(spec or "").split(",") if item.strip()]
    columns: list[tuple[str, str]] = []
    for part in parts:
        if ":" not in part:
            continue
        header, _, path = part.partition(":")
        columns.append((header.strip(), path.strip()))
    if not columns:
        return ""

    lines: list[str] = []
    if not no_headers:
        lines.append(" ".join(header for header, _ in columns))
    for row in rows:
        values: list[str] = []
        for _, path in columns:
            value = _extract_path_value(row, path)
            if isinstance(value, bool):
                values.append("true" if value else "false")
            elif value is None:
                values.append("")
            else:
                values.append(str(value))
        lines.append(" ".join(values).rstrip())
    return "\n".join(line for line in lines if line is not None).rstrip() + ("\n" if lines else "")


@dataclass
class KubernetesClient:
    cmd_prefix: list[str]
    runner: CommandRunner
    _k8s_client: Any = field(default=None, init=False, repr=False)
    _k8s_config: Any = field(default=None, init=False, repr=False)
    _k8s_dynamic: Any = field(default=None, init=False, repr=False)
    _k8s_stream: Any = field(default=None, init=False, repr=False)
    _api_client: Any = field(default=None, init=False, repr=False)
    _core_v1: Any = field(default=None, init=False, repr=False)
    _apps_v1: Any = field(default=None, init=False, repr=False)
    _batch_v1: Any = field(default=None, init=False, repr=False)
    _networking_v1: Any = field(default=None, init=False, repr=False)
    _dynamic_client: Any = field(default=None, init=False, repr=False)

    @classmethod
    def from_environment(cls) -> "KubernetesClient":
        override = os.environ.get("KUBECTL_CMD", "").strip()
        if override:
            return cls(cmd_prefix=override.split(), runner=CommandRunner())
        # Keep historical default for messages/help text while runtime calls use API.
        return cls(cmd_prefix=resolve_kubectl_binary(), runner=CommandRunner())

    def _ensure_clients(self) -> None:
        if self._core_v1 is not None:
            return
        try:
            from kubernetes import client as k8s_client  # type: ignore
            from kubernetes import config as k8s_config  # type: ignore
            from kubernetes import dynamic as k8s_dynamic  # type: ignore
            from kubernetes import stream as k8s_stream  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised in integration
            raise ConfigError(
                "The Kubernetes Python client is required. " "Install with: pip install kubernetes"
            ) from exc

        try:
            k8s_config.load_incluster_config()
        except Exception:
            try:
                k8s_config.load_kube_config()
            except Exception as exc:  # pragma: no cover - exercised in integration
                raise KubernetesError(
                    "Could not load Kubernetes configuration via in-cluster "
                    "config or kubeconfig."
                ) from exc

        configuration = k8s_client.Configuration.get_default_copy()
        verify_ssl = _env_truthy("MEDIA_STACK_K8S_VERIFY_SSL", default=True)
        if not verify_ssl:
            configuration.verify_ssl = False
            if _env_truthy("MEDIA_STACK_K8S_SUPPRESS_INSECURE_WARNINGS", default=True):
                try:
                    import urllib3

                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                except Exception:
                    pass

        api_client = k8s_client.ApiClient(configuration=configuration)
        self._k8s_client = k8s_client
        self._k8s_config = k8s_config
        self._k8s_dynamic = k8s_dynamic
        self._k8s_stream = k8s_stream
        self._api_client = api_client
        self._core_v1 = k8s_client.CoreV1Api(api_client)
        self._apps_v1 = k8s_client.AppsV1Api(api_client)
        self._batch_v1 = k8s_client.BatchV1Api(api_client)
        self._networking_v1 = k8s_client.NetworkingV1Api(api_client)
        self._dynamic_client = k8s_dynamic.DynamicClient(api_client)

    def _result(
        self,
        args: list[str],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> CommandResult:
        return CommandResult(
            args=[*self.cmd_prefix, *args], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def _error_result(self, args: list[str], message: str, *, returncode: int = 1) -> CommandResult:
        return self._result(args, returncode=returncode, stdout="", stderr=f"{message.rstrip()}\n")

    def run(
        self,
        args: Iterable[str],
        *,
        check: bool = True,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        command_args = list(args)
        try:
            result = self._run_api_command(
                command_args,
                timeout=timeout,
                env=env,
                input_text=input_text,
            )
        except CommandExecutionError as exc:
            raise KubernetesError(str(exc)) from exc
        except Exception as exc:
            if isinstance(exc, KubernetesError):
                if check:
                    raise
                return self._error_result(command_args, str(exc))
            if check:
                raise KubernetesError(str(exc)) from exc
            return self._error_result(command_args, str(exc))

        if check and result.returncode != 0:
            raise KubernetesError(
                result.stderr.strip() or result.stdout.strip() or "Kubernetes command failed."
            )
        return result

    @timed("kube.api.run")
    @retry(
        attempts=KUBECTL_RETRY_ATTEMPTS,
        delay_seconds=KUBECTL_RETRY_DELAY_SECONDS,
        max_delay_seconds=KUBECTL_RETRY_MAX_DELAY_SECONDS,
        backoff_multiplier=KUBECTL_RETRY_BACKOFF,
        retry_if=_is_retryable_kubectl_error,
        logger=logging.getLogger("media_stack"),
        operation="kube.api.run",
    )
    def _run_api_command(
        self,
        args: list[str],
        *,
        timeout: int | None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        del env  # kept for interface compatibility
        self._ensure_clients()
        namespace = None
        cursor = 0
        parsed = list(args)
        while cursor < len(parsed):
            token = parsed[cursor]
            if token in ("-n", "--namespace") and cursor + 1 < len(parsed):
                namespace = parsed[cursor + 1]
                del parsed[cursor : cursor + 2]
                continue
            break

        if not parsed:
            return self._error_result(args, "Missing Kubernetes command.", returncode=2)

        command = parsed[0]
        remainder = parsed[1:]
        ns = str(namespace or os.environ.get("NAMESPACE") or "default")

        if command == "get":
            return self._run_get(args, ns, remainder)
        if command == "describe":
            return self._run_describe(args, ns, remainder)
        if command == "logs":
            return self._run_logs(args, ns, remainder)
        if command == "rollout":
            return self._run_rollout(args, ns, remainder, timeout=timeout)
        if command == "patch":
            return self._run_patch(args, ns, remainder)
        if command == "delete":
            return self._run_delete(args, ns, remainder)
        if command == "exec":
            return self._run_exec(args, ns, remainder)
        if command == "scale":
            return self._run_scale(args, ns, remainder)
        if command in ("apply", "create", "replace"):
            return self._run_manifest_command(
                args,
                ns,
                command,
                remainder,
                input_text=input_text,
            )

        return self._error_result(
            args,
            f"Unsupported Kubernetes command via API adapter: {' '.join(args)}",
            returncode=2,
        )

    def _dynamic_get_resource(self, api_version: str, kind: str) -> Any:
        return self._dynamic_client.resources.get(api_version=api_version, kind=kind)

    def _iter_yaml_objects(self, manifest_path: str) -> list[dict[str, Any]]:
        path = Path(manifest_path)
        if not path.exists():
            raise ConfigError(f"Manifest path not found: {path}")
        raw = path.read_text(encoding="utf-8")
        return self._iter_yaml_objects_from_text(raw, source=str(path))

    def _iter_yaml_objects_from_text(self, raw: str, *, source: str) -> list[dict[str, Any]]:
        objects = [item for item in yaml.safe_load_all(raw) if isinstance(item, dict)]
        if not objects:
            raise ConfigError(f"No Kubernetes objects found in manifest: {source}")
        return objects

    def _normalize_object_namespace(self, obj: dict[str, Any], namespace: str) -> dict[str, Any]:
        metadata = obj.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            obj["metadata"] = metadata
        if namespace:
            metadata.setdefault("namespace", namespace)
        return obj

    def _apply_object(self, obj: dict[str, Any], *, namespace: str) -> str:
        api_version = str(obj.get("apiVersion") or "").strip()
        kind = str(obj.get("kind") or "").strip()
        if not api_version or not kind:
            raise ConfigError("Manifest object missing required apiVersion/kind.")
        body = self._normalize_object_namespace(dict(obj), namespace)
        metadata = body.get("metadata") or {}
        name = str((metadata or {}).get("name") or "").strip()
        if not name:
            raise ConfigError("Manifest object missing metadata.name.")

        resource = self._dynamic_get_resource(api_version, kind)
        kwargs: dict[str, Any] = {"name": name, "body": body}
        if getattr(resource, "namespaced", False):
            kwargs["namespace"] = str((metadata or {}).get("namespace") or namespace or "default")
        resource.patch(
            content_type="application/apply-patch+yaml",
            field_manager="media-stack",
            force=True,
            **kwargs,
        )
        return f"{kind.lower()}/{name} configured"

    def _replace_object(self, obj: dict[str, Any], *, namespace: str) -> str:
        api_version = str(obj.get("apiVersion") or "").strip()
        kind = str(obj.get("kind") or "").strip()
        body = self._normalize_object_namespace(dict(obj), namespace)
        metadata = body.get("metadata") or {}
        name = str((metadata or {}).get("name") or "").strip()
        if not api_version or not kind or not name:
            raise ConfigError("Manifest object missing required apiVersion/kind/metadata.name.")

        resource = self._dynamic_get_resource(api_version, kind)
        kwargs: dict[str, Any] = {"name": name, "body": body}
        if getattr(resource, "namespaced", False):
            kwargs["namespace"] = str((metadata or {}).get("namespace") or namespace or "default")
        resource.replace(**kwargs)
        return f"{kind.lower()}/{name} replaced"

    def _create_object(self, obj: dict[str, Any], *, namespace: str) -> str:
        api_version = str(obj.get("apiVersion") or "").strip()
        kind = str(obj.get("kind") or "").strip()
        body = self._normalize_object_namespace(dict(obj), namespace)
        metadata = body.get("metadata") or {}
        name = str((metadata or {}).get("name") or "").strip()
        if not api_version or not kind:
            raise ConfigError("Manifest object missing required apiVersion/kind.")

        resource = self._dynamic_get_resource(api_version, kind)
        kwargs: dict[str, Any] = {"body": body}
        if getattr(resource, "namespaced", False):
            kwargs["namespace"] = str((metadata or {}).get("namespace") or namespace or "default")
        resource.create(**kwargs)
        if name:
            return f"{kind.lower()}/{name} created"
        return f"{kind.lower()} created"

    def _run_manifest_command(
        self,
        args: list[str],
        namespace: str,
        command: str,
        remainder: list[str],
        *,
        input_text: str | None,
    ) -> CommandResult:
        if command == "create" and remainder and remainder[0] == "configmap":
            return self._run_create_configmap(args, namespace, remainder[1:])

        if command == "create" and remainder and remainder[0] == "namespace":
            ns_name = remainder[1] if len(remainder) > 1 else ""
            if ns_name:
                return self._run_create_namespace(args, ns_name)

        if len(remainder) < 2 or remainder[0] != "-f":
            return self._error_result(
                args,
                f"Unsupported manifest command: {' '.join(args)}",
                returncode=2,
            )

        manifest_path = remainder[1]
        if manifest_path == "-":
            if input_text is None:
                return self._error_result(
                    args,
                    "Manifest stdin payload required for '-f -'.",
                    returncode=2,
                )
            objects = self._iter_yaml_objects_from_text(input_text, source="stdin")
        else:
            objects = self._iter_yaml_objects(manifest_path)
        messages: list[str] = []
        try:
            for obj in objects:
                if command == "apply":
                    messages.append(self._apply_object(obj, namespace=namespace))
                elif command == "replace":
                    messages.append(self._replace_object(obj, namespace=namespace))
                elif command == "create":
                    messages.append(self._create_object(obj, namespace=namespace))
        except Exception as exc:
            status, message = _format_api_error(exc)
            return self._error_result(args, message, returncode=status or 1)
        out = "\n".join(messages).rstrip()
        return self._result(args, stdout=(out + "\n") if out else "")

    def _run_create_namespace(
        self,
        args: list[str],
        ns_name: str,
    ) -> CommandResult:
        body = self._k8s_client.V1Namespace(
            metadata=self._k8s_client.V1ObjectMeta(name=ns_name)
        )
        try:
            self._core_v1.create_namespace(body=body)
            return self._result(args, stdout=f"namespace/{ns_name} created\n")
        except Exception as exc:
            status, message = _format_api_error(exc)
            if "already exists" in str(message).lower():
                return self._result(
                    args,
                    stdout=f"namespace/{ns_name} already exists\n",
                    stderr=f"{message}\n",
                    returncode=0,
                )
            return self._error_result(args, message, returncode=status or 1)

    def _run_create_configmap(
        self,
        args: list[str],
        namespace: str,
        remainder: list[str],
    ) -> CommandResult:
        if not remainder:
            return self._error_result(args, "Missing ConfigMap name.", returncode=2)
        name = str(remainder[0] or "").strip()
        from_files: list[tuple[str, str]] = []
        output = ""
        dry_run = False

        cursor = 1
        while cursor < len(remainder):
            token = remainder[cursor]
            if token.startswith("--from-file="):
                pair = token.split("=", 1)[1]
                key, _, path = pair.partition("=")
                if not path:
                    path = key
                    key = Path(path).name
                from_files.append((key, path))
                cursor += 1
                continue
            if token == "--from-file" and cursor + 1 < len(remainder):
                pair = remainder[cursor + 1]
                key, _, path = pair.partition("=")
                if not path:
                    path = key
                    key = Path(path).name
                from_files.append((key, path))
                cursor += 2
                continue
            if token.startswith("--dry-run="):
                dry_run = token.split("=", 1)[1].strip().lower() == "client"
                cursor += 1
                continue
            if token == "--dry-run":
                dry_run = True
                cursor += 1
                continue
            if token == "-o" and cursor + 1 < len(remainder):
                output = str(remainder[cursor + 1] or "")
                cursor += 2
                continue
            if token.startswith("-o="):
                output = token.split("=", 1)[1]
                cursor += 1
                continue
            cursor += 1

        data: dict[str, str] = {}
        for key, file_path in from_files:
            path = Path(file_path)
            if not path.exists():
                return self._error_result(
                    args, f"ConfigMap source file not found: {path}", returncode=1
                )
            data[str(key)] = path.read_text(encoding="utf-8")

        body = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": namespace},
            "data": data,
        }

        if dry_run and output == "yaml":
            return self._result(args, stdout=yaml.safe_dump(body, sort_keys=False))

        try:
            self._core_v1.create_namespaced_config_map(namespace=namespace, body=body)
        except Exception as exc:
            status, message = _format_api_error(exc)
            return self._error_result(args, message, returncode=status or 1)
        return self._result(args, stdout=f"configmap/{name} created\n")

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
            except Exception:
                pass
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
