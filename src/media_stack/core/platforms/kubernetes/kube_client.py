"""Kubernetes API adapter.

This module keeps the historical `KubernetesClient.run([...])` interface used by
CLI services, but executes against the official Kubernetes Python client
(`kubernetes-client/python`) instead of shelling out to kubectl.

The implementation is split across focused submodules using a mixin pattern:
  - kube_helpers.py   -- shared constants, utility functions
  - kube_manifests.py -- apply/create/replace manifest operations
  - kube_query.py     -- get/describe/logs operations
  - kube_workloads.py -- rollout/scale/patch/delete/exec operations

All public names remain importable from this module for backward compatibility.
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from media_stack.core.decorators import retry, timed
from media_stack.core.exceptions import ConfigError, KubernetesError
from media_stack.core.platforms.kubernetes.kube_helpers import (
    KUBECTL_RETRY_ATTEMPTS,
    KUBECTL_RETRY_BACKOFF,
    KUBECTL_RETRY_DELAY_SECONDS,
    KUBECTL_RETRY_MAX_DELAY_SECONDS,
    _env_truthy,
    _extract_path_value,
    _format_api_error,
    _is_retryable_kubectl_error,
    _parse_jsonpath_key,
    _parse_timeout_seconds,
    _render_custom_columns,
    _selector_from_match_labels,
    resolve_kubectl_binary,
)
from media_stack.core.platforms.kubernetes.kube_manifests import ManifestsMixin
from media_stack.core.platforms.kubernetes.kube_query import QueryMixin
from media_stack.core.platforms.kubernetes.kube_workloads import WorkloadsMixin
from media_stack.core.subprocess_utils import CommandExecutionError, CommandResult, CommandRunner

# Re-export all public helper names so ``from ...kube_client import X`` keeps working.
__all__ = [
    "KUBECTL_RETRY_ATTEMPTS",
    "KUBECTL_RETRY_BACKOFF",
    "KUBECTL_RETRY_DELAY_SECONDS",
    "KUBECTL_RETRY_MAX_DELAY_SECONDS",
    "KubernetesClient",
    "_env_truthy",
    "_extract_path_value",
    "_format_api_error",
    "_is_retryable_kubectl_error",
    "_parse_jsonpath_key",
    "_parse_timeout_seconds",
    "_render_custom_columns",
    "_selector_from_match_labels",
    "resolve_kubectl_binary",
]


@dataclass
class KubernetesClient(ManifestsMixin, QueryMixin, WorkloadsMixin):
    """Kubernetes API client adapter.

    Combines base command dispatch with mixin-provided operations:
      - ManifestsMixin  -- apply/create/replace/configmap/namespace
      - QueryMixin      -- get/describe/logs
      - WorkloadsMixin  -- rollout/scale/patch/delete/exec
    """

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
                except Exception as exc:
                    log_swallowed(exc)

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
