"""Kubectl adapter."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from typing import Iterable, Mapping

from .decorators import retry, timed
from .exceptions import ConfigError, KubernetesError
from .subprocess_utils import CommandExecutionError, CommandResult, CommandRunner

KUBECTL_RETRY_ATTEMPTS = max(1, int(os.environ.get("MEDIA_STACK_KUBECTL_RETRY_ATTEMPTS", "3")))
KUBECTL_RETRY_DELAY_SECONDS = float(
    os.environ.get("MEDIA_STACK_KUBECTL_RETRY_DELAY_SECONDS", "0.5")
)
KUBECTL_RETRY_MAX_DELAY_SECONDS = float(
    os.environ.get("MEDIA_STACK_KUBECTL_RETRY_MAX_DELAY_SECONDS", "3")
)
KUBECTL_RETRY_BACKOFF = float(os.environ.get("MEDIA_STACK_KUBECTL_RETRY_BACKOFF", "2"))


def _is_retryable_kubectl_error(exc: Exception) -> bool:
    if not isinstance(exc, CommandExecutionError):
        return False

    output = f"{exc.stderr}\n{exc.stdout}".lower()
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
    return any(marker in output for marker in retryable_markers)


def resolve_kubectl_binary() -> list[str]:
    if shutil.which("microk8s"):
        return ["microk8s", "kubectl"]
    if shutil.which("kubectl"):
        return ["kubectl"]
    raise ConfigError("Neither microk8s nor kubectl is available in PATH.")


@dataclass
class KubectlClient:
    cmd_prefix: list[str]
    runner: CommandRunner

    @classmethod
    def from_environment(cls) -> "KubectlClient":
        override = os.environ.get("KUBECTL_CMD", "").strip()
        if override:
            return cls(cmd_prefix=override.split(), runner=CommandRunner())
        return cls(cmd_prefix=resolve_kubectl_binary(), runner=CommandRunner())

    def run(
        self,
        args: Iterable[str],
        *,
        check: bool = True,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        command = [*self.cmd_prefix, *list(args)]
        try:
            return self._run_command(command, check=check, env=env, timeout=timeout)
        except CommandExecutionError as exc:
            raise KubernetesError(str(exc)) from exc

    @timed("kubectl.run")
    @retry(
        attempts=KUBECTL_RETRY_ATTEMPTS,
        delay_seconds=KUBECTL_RETRY_DELAY_SECONDS,
        max_delay_seconds=KUBECTL_RETRY_MAX_DELAY_SECONDS,
        backoff_multiplier=KUBECTL_RETRY_BACKOFF,
        retry_if=_is_retryable_kubectl_error,
        logger=logging.getLogger("media_stack"),
        operation="kubectl.run",
    )
    def _run_command(
        self,
        args: list[str],
        *,
        check: bool,
        env: Mapping[str, str] | None,
        timeout: int | None,
    ) -> CommandResult:
        return self.runner.run(
            args,
            check=check,
            env=env,
            timeout=timeout,
        )
