"""Kubernetes-side implementation of the ``ContainerAccess`` domain port.

ADR-0013 Phase 3b. Sister of the compose impl at
``infrastructure/platforms/compose/container_access.py``: same
Protocol surface (``read_logs`` / ``exec_shell`` / ``restart``),
backed by ``kubectl`` instead of docker-py.

We shell out to the ``kubectl`` binary rather than the Python
``kubernetes`` client because:

* the existing CLI tooling (``services/apps/.../cli/*.py``) already
  uses ``kubectl`` via ``cli_common.kube_cmd()`` — one toolchain.
* ``kubectl exec`` handles tty + signal propagation correctly
  out of the box; the ``kubernetes.stream`` API is fiddly to
  drive correctly for short-lived shells.
* the Python client requires extra in-cluster vs out-of-cluster
  config wiring which is already centralized in ``kubectl``'s
  built-in resolver.

The handle here is the **pod selector** (e.g. ``deploy/qbittorrent``
or ``-l app=qbittorrent``) plus a namespace. ``LifecycleResolver``
resolves a deployment-name → selector at context-build time so the
ensurer doesn't have to know that contract.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from typing import Mapping, Sequence

from media_stack.domain.services.container_access import (
    ContainerAccess,
    ContainerAccessError,
)


logger = logging.getLogger(__name__)


class K8sContainerAccess:
    """``ContainerAccess`` backed by ``kubectl`` against a pod selector.

    Parameters:
      kube_cmd: tokens of the kubectl binary path, e.g. ``["kubectl"]``
        or ``["microk8s", "kubectl"]``. The exact list returned by
        ``cli_common.kube_cmd()`` so this class doesn't need to
        re-resolve the binary.
      namespace: the k8s namespace the workload lives in. Required —
        defaulting to ``default`` would silently target the wrong
        cluster on a multi-tenant host.
      selector: a kubectl-recognizable target. Use ``deploy/<name>``
        for stable cross-pod selection (kubectl picks an arbitrary
        ready pod) or ``pod/<name>`` for a specific pod. The
        lifecycle layer doesn't pin a single pod by default.
    """

    def __init__(
        self,
        *,
        kube_cmd: Sequence[str],
        namespace: str,
        selector: str,
    ) -> None:
        self._kube_cmd: list[str] = list(kube_cmd)
        self._namespace = str(namespace)
        self._selector = str(selector)

    def read_logs(self, *, tail_lines: int = 600) -> str:
        cmd = [
            *self._kube_cmd,
            "-n", self._namespace,
            "logs",
            self._selector,
            "--tail", str(int(max(1, tail_lines))),
            "--all-containers=true",
        ]
        try:
            proc = subprocess.run(
                cmd, check=False, capture_output=True, text=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContainerAccessError(
                f"kubectl logs failed: {exc}"
            ) from exc
        if proc.returncode != 0:
            logger.debug(
                "kubectl logs %s -n %s returned %d: %s",
                self._selector, self._namespace, proc.returncode,
                (proc.stderr or "").strip(),
            )
            return ""
        return proc.stdout or ""

    def exec_shell(
        self,
        script: str,
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> tuple[int, str]:
        # Build a ``sh -lc <script>`` invocation. Environment vars
        # threaded via ``env <K=V> ... sh -lc`` so they don't appear
        # in the ``kubectl`` argv (which would land in audit logs).
        env_assignments = [
            f"{k}={shlex.quote(str(v))}" for k, v in (env or {}).items()
        ]
        inner_cmd = "sh -lc " + shlex.quote(script)
        wrapped_cmd = (
            f"env {' '.join(env_assignments)} {inner_cmd}"
            if env_assignments else inner_cmd
        )
        cmd = [
            *self._kube_cmd,
            "-n", self._namespace,
            "exec",
            self._selector,
            "--",
            "sh", "-lc", wrapped_cmd,
        ]
        try:
            proc = subprocess.run(
                cmd, check=False, capture_output=True, text=True,
                timeout=int(max(1, timeout_seconds)),
            )
        except subprocess.TimeoutExpired as exc:
            raise ContainerAccessError(
                f"kubectl exec timed out after {timeout_seconds}s"
            ) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContainerAccessError(
                f"kubectl exec failed: {exc}"
            ) from exc
        output = (proc.stdout or "") + (proc.stderr or "")
        return int(proc.returncode), output

    def restart(self, *, timeout_seconds: int = 10) -> bool:
        # ``kubectl rollout restart`` is the canonical "graceful bounce
        # of a deployment's pods". Deletes pods one by one respecting
        # the deployment's update strategy. Falls back to delete-pod
        # for ``pod/`` selectors that aren't deployments.
        if self._selector.startswith("deploy/") or self._selector.startswith(
            "deployment/",
        ):
            cmd = [
                *self._kube_cmd,
                "-n", self._namespace,
                "rollout", "restart", self._selector,
            ]
        else:
            cmd = [
                *self._kube_cmd,
                "-n", self._namespace,
                "delete", "--ignore-not-found", self._selector,
            ]
        try:
            proc = subprocess.run(
                cmd, check=False, capture_output=True, text=True,
                timeout=int(max(1, timeout_seconds)),
            )
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0


# Compile-time + runtime check.
def _typecheck(value: K8sContainerAccess) -> ContainerAccess:
    return value


__all__ = ["K8sContainerAccess"]
